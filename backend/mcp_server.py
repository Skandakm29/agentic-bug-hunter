from pathlib import Path
import os
import json
import re
import csv
import requests
from typing import Optional
from fastmcp import FastMCP
from groq import Groq

# ── Groq client ──────────────────────────────
groq_client = Groq(api_key=os.environ.get("GROQ_API_KEY", ""))
GROQ_MODEL  = "llama3-8b-8192"

# ── Optional RAG setup ───────────────────────
rag_available = False
retriever = None

try:
    from llama_index.embeddings.huggingface import HuggingFaceEmbedding
    from llama_index.core import StorageContext, load_index_from_storage, Settings
    from llama_index.core.retrievers import VectorIndexRetriever

    current_directory = os.getcwd()
    if os.path.basename(current_directory) == "server":
        directory_path = os.path.join(".", "embedding_model")
        storage_path   = os.path.join(".", "storage")
    else:
        directory_path = os.path.join(".", "server", "embedding_model")
        storage_path   = os.path.join(".", "server", "storage")

    if Path(directory_path).is_dir() and Path(storage_path).is_dir():
        embed_model = HuggingFaceEmbedding(model_name=directory_path)
        Settings.embed_model = embed_model
        storage_context = StorageContext.from_defaults(persist_dir=storage_path)
        index = load_index_from_storage(storage_context=storage_context)
        retriever = VectorIndexRetriever(index=index, similarity_top_k=20)
        rag_available = True
        print("✅ RAG index loaded")
    else:
        print("⚠️  RAG storage not found — search_documents unavailable")
except Exception as e:
    print(f"⚠️  RAG setup skipped: {e}")

# ────────────────────────────────────────────
# Static Analysis Engine
# ────────────────────────────────────────────

KNOWN_PREFIXES = [
    "get", "set", "read", "write", "pin", "label", "burst", "execute",
    "vForce", "iForce", "vMeas", "iMeas", "samples", "begin", "end", "wait"
]

def detect_unknown_methods(code):
    findings = []
    for i, line in enumerate(code.split("\n")):
        for method in re.findall(r'\.\s*(\w+)\s*\(', line):
            if not any(method.startswith(p) for p in KNOWN_PREFIXES):
                findings.append({"line_number": i+1, "line_text": line.strip(),
                    "rule_tag": "suspicious_method_name", "static_confidence": 0.8,
                    "description": f"Unknown RDI method: '{method}'"})
    return findings

def detect_unmatched_rdi_blocks(code):
    b, e = code.count("RDI_BEGIN"), code.count("RDI_END")
    if b != e:
        return [{"line_number": None, "line_text": "RDI_BEGIN/RDI_END mismatch",
                 "rule_tag": "rdi_block_mismatch", "static_confidence": 0.9,
                 "description": f"Found {b} RDI_BEGIN but {e} RDI_END"}]
    return []

def detect_incomplete_chaining(code):
    findings = []
    for i, line in enumerate(code.split("\n")):
        if "rdi." in line and not line.strip().endswith((";", "}", "{")):
            findings.append({"line_number": i+1, "line_text": line.strip(),
                "rule_tag": "incomplete_chain", "static_confidence": 0.7,
                "description": "Incomplete method chain — missing terminator"})
    return findings

def detect_overflow_risk(code):
    findings = []
    for i, line in enumerate(code.split("\n")):
        if "uint8_t" in line and any(k in line for k in ["total","sum","avg","count","acc"]):
            findings.append({"line_number": i+1, "line_text": line.strip(),
                "rule_tag": "overflow_risk", "static_confidence": 0.75,
                "description": "uint8_t accumulator will overflow for values > 255"})
        if re.search(r'\bint\b', line) and "sensor" in line.lower():
            findings.append({"line_number": i+1, "line_text": line.strip(),
                "rule_tag": "type_mismatch", "static_confidence": 0.6,
                "description": "Signed int for sensor value — consider uint16_t/uint32_t"})
    return findings

def detect_missing_volatile(code):
    findings = []
    for i, line in enumerate(code.split("\n")):
        if re.search(r'\b(bool|uint8_t|int)\b', line) and "=" in line and "volatile" not in line:
            if any(k in line for k in ["flag","ready","done","busy","irq","data_ready","isr"]):
                findings.append({"line_number": i+1, "line_text": line.strip(),
                    "rule_tag": "missing_volatile", "static_confidence": 0.85,
                    "description": "ISR-shared variable missing 'volatile'"})
    return findings

def detect_null_pointer(code):
    findings, declared, allocated = [], set(), set()
    for i, line in enumerate(code.split("\n")):
        for p in re.findall(r'\b\w+\s*\*\s*(\w+)\s*;', line):
            declared.add(p)
        if any(k in line for k in ["malloc","new ","calloc"]):
            allocated.update(declared)
        for p in re.findall(r'\*(\w+)\s*=', line):
            if p in declared and p not in allocated:
                findings.append({"line_number": i+1, "line_text": line.strip(),
                    "rule_tag": "null_pointer", "static_confidence": 0.9,
                    "description": f"Pointer '{p}' dereferenced without allocation"})
    return findings

def detect_blocking_delay(code):
    findings, in_isr = [], False
    for i, line in enumerate(code.split("\n")):
        if re.search(r'\bISR\b|\bIRQ\b|interrupt|IRAM_ATTR|__irq', line, re.IGNORECASE):
            in_isr = True
        if in_isr and re.search(r'delay|HAL_Delay|sleep|busy_wait|while\s*\(', line):
            findings.append({"line_number": i+1, "line_text": line.strip(),
                "rule_tag": "blocking_in_isr", "static_confidence": 0.85,
                "description": "Blocking call inside interrupt context"})
        if "}" in line:
            in_isr = False
    return findings

def detect_bit_manipulation_error(code):
    findings = []
    for i, line in enumerate(code.split("\n")):
        if re.search(r'&\s*\d+\b', line) and "~" not in line:
            if any(k in line.lower() for k in ["clear","disable","off"]):
                findings.append({"line_number": i+1, "line_text": line.strip(),
                    "rule_tag": "bit_clear_error", "static_confidence": 0.8,
                    "description": "Use '& ~mask' not '& mask' to clear a bit"})
    return findings

def run_static_analysis(code: str) -> list:
    findings = []
    findings.extend(detect_unknown_methods(code))
    findings.extend(detect_unmatched_rdi_blocks(code))
    findings.extend(detect_incomplete_chaining(code))
    findings.extend(detect_overflow_risk(code))
    findings.extend(detect_missing_volatile(code))
    findings.extend(detect_null_pointer(code))
    findings.extend(detect_blocking_delay(code))
    findings.extend(detect_bit_manipulation_error(code))
    return findings

# ────────────────────────────────────────────
# LLM Validator — Groq (llama3-8b)
# ────────────────────────────────────────────

def call_llm(code: str, finding: dict) -> Optional[dict]:
    prompt = f"""You are an expert embedded systems C++ engineer.
Analyze this code for bugs: integer overflow, missing volatile, null pointers,
stack overflow, incorrect bit manipulation, blocking delays in ISR, etc.

Suspicious area (line {finding.get("line_number","?")}):
{finding["line_text"]}

Full Code:
{code}

Respond ONLY in JSON, no extra text, no markdown:
{{
  "valid_bug": true,
  "explanation": "...",
  "corrected_code": "...",
  "confidence": 0.0
}}"""

    try:
        response = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=1024
        )
        raw = response.choices[0].message.content
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception as e:
        print(f"[LLM] Groq error: {e}")
    return None

def orchestrate(code: str) -> dict:
    findings = run_static_analysis(code)
    best = max(findings, key=lambda x: x["static_confidence"]) if findings else {
        "line_number": None, "line_text": "General code review",
        "rule_tag": "semantic_review", "static_confidence": 0.5
    }
    validation = call_llm(code, best)
    llm_result = None
    if validation:
        conf = round(0.6 * float(validation.get("confidence", 0)) + 0.4 * best["static_confidence"], 3)
        llm_result = {
            "valid_bug": validation.get("valid_bug", False),
            "explanation": validation.get("explanation", ""),
            "corrected_code": validation.get("corrected_code", code),
            "confidence": conf
        }
    return {
        "static_findings": findings,
        "llm_result": llm_result,
        "total_issues": len(findings),
        "llm_available": llm_result is not None
    }

# ────────────────────────────────────────────
# MCP Server
# ────────────────────────────────────────────

mcp = FastMCP("AgenticBugHunter")

@mcp.tool()
def analyze_code(code: str) -> dict:
    """
    Analyze C++ / RDI code for bugs using static analysis + Groq LLM validation.
    Returns static findings, LLM explanation, corrected code, and confidence score.
    """
    print(f"[MCP] analyze_code called ({len(code)} chars)")
    return orchestrate(code)

@mcp.tool()
def batch_analyze(samples_csv_path: str) -> dict:
    """
    Run bug analysis on all code samples in a CSV file.
    CSV must have a 'code' column.
    """
    print(f"[MCP] batch_analyze: {samples_csv_path}")
    if not Path(samples_csv_path).exists():
        return {"error": f"File not found: {samples_csv_path}"}
    results = []
    try:
        with open(samples_csv_path, newline="", encoding="utf-8") as f:
            for i, row in enumerate(csv.DictReader(f)):
                code = row.get("code", "")
                if not code.strip():
                    continue
                result = orchestrate(code)
                results.append({
                    "sample_index": i,
                    "code_preview": code[:80] + "..." if len(code) > 80 else code,
                    "total_issues": result["total_issues"],
                    "rule_tags": [f["rule_tag"] for f in result["static_findings"]],
                    "llm_valid_bug": result["llm_result"]["valid_bug"] if result["llm_result"] else None,
                    "llm_confidence": result["llm_result"]["confidence"] if result["llm_result"] else None,
                })
    except Exception as e:
        return {"error": str(e)}

    total = len(results)
    bugs  = sum(1 for r in results if r["total_issues"] > 0)
    return {
        "total_samples": total, "bugs_found": bugs, "clean_samples": total - bugs,
        "results": results,
        "summary_stats": {
            "bug_rate": f"{bugs/total*100:.1f}%" if total else "0%",
            "avg_issues": round(sum(r["total_issues"] for r in results) / total, 2) if total else 0
        }
    }

@mcp.tool()
def get_static_rules() -> list:
    """Returns all available static analysis rules with descriptions and confidence levels."""
    return [
        {"rule": "suspicious_method_name", "confidence": 0.80, "description": "Unknown RDI API method call"},
        {"rule": "rdi_block_mismatch",      "confidence": 0.90, "description": "RDI_BEGIN / RDI_END count mismatch"},
        {"rule": "incomplete_chain",         "confidence": 0.70, "description": "rdi. call without terminating semicolon"},
        {"rule": "overflow_risk",            "confidence": 0.75, "description": "uint8_t used for accumulation"},
        {"rule": "type_mismatch",            "confidence": 0.60, "description": "Signed int used for sensor values"},
        {"rule": "missing_volatile",         "confidence": 0.85, "description": "ISR-shared variable missing volatile"},
        {"rule": "null_pointer",             "confidence": 0.90, "description": "Pointer dereferenced without allocation"},
        {"rule": "blocking_in_isr",          "confidence": 0.85, "description": "Blocking delay inside interrupt handler"},
        {"rule": "bit_clear_error",          "confidence": 0.80, "description": "Incorrect bit clear: & mask vs & ~mask"},
    ]

@mcp.tool()
def get_server_status() -> dict:
    """Returns current status of all components: static engine, Groq LLM, and RAG index."""
    groq_ok = bool(os.environ.get("GROQ_API_KEY", ""))
    return {
        "static_engine": {"status": "online", "rules": 9},
        "llm": {"status": "online" if groq_ok else "no_api_key", "provider": "Groq", "model": GROQ_MODEL},
        "rag_index": {"status": "online" if rag_available else "offline"},
        "mcp_server": {"status": "online", "port": int(os.environ.get("MCP_PORT", 8003)), "transport": "sse"}
    }

@mcp.tool()
def search_documents(query: str) -> list:
    """Search the RDI API documentation using vector similarity (RAG)."""
    if not rag_available or retriever is None:
        return [{"error": "RAG index not available"}]
    nodes = retriever.retrieve(query)
    return [{"text": n.get_text(), "score": n.get_score()} for n in nodes]

if __name__ == "__main__":
    print("=" * 50)
    print("  Agentic Bug Hunter — MCP Server")
    print(f"  Port     : {os.environ.get('MCP_PORT', 8003)}")
    print(f"  LLM      : Groq / {GROQ_MODEL}")
    print(f"  RAG      : {'enabled' if rag_available else 'disabled'}")
    print(f"  API Key  : {'set ✅' if os.environ.get('GROQ_API_KEY') else 'missing ❌'}")
    print("=" * 50)
    mcp.run(transport="sse")
