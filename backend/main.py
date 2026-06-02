from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import os, json, re, csv, requests
from pathlib import Path
from groq import Groq

app = FastAPI(title="Agentic Bug Hunter API", version="2.0.0")

FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3000")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL, "http://localhost:3000"],
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

# ── Groq ────────────────────────────────────
groq_client = Groq(api_key=os.environ.get("GROQ_API_KEY", ""))
GROQ_MODEL  = "llama3-8b-8192"

# ── Static Engine ────────────────────────────
KNOWN_PREFIXES = ["get","set","read","write","pin","label","burst","execute",
                  "vForce","iForce","vMeas","iMeas","samples","begin","end","wait"]

def detect_unknown_methods(code):
    findings = []
    for i, line in enumerate(code.split("\n")):
        for method in re.findall(r'\.\s*(\w+)\s*\(', line):
            if not any(method.startswith(p) for p in KNOWN_PREFIXES):
                findings.append({"line_number":i+1,"line_text":line.strip(),
                    "rule_tag":"suspicious_method_name","static_confidence":0.8,
                    "description":f"Unknown RDI method: '{method}'"})
    return findings

def detect_unmatched_rdi_blocks(code):
    b, e = code.count("RDI_BEGIN"), code.count("RDI_END")
    if b != e:
        return [{"line_number":None,"line_text":"RDI_BEGIN/RDI_END mismatch",
                 "rule_tag":"rdi_block_mismatch","static_confidence":0.9,
                 "description":f"Found {b} RDI_BEGIN but {e} RDI_END"}]
    return []

def detect_incomplete_chaining(code):
    findings = []
    for i, line in enumerate(code.split("\n")):
        if "rdi." in line and not line.strip().endswith((";","}", "{")):
            findings.append({"line_number":i+1,"line_text":line.strip(),
                "rule_tag":"incomplete_chain","static_confidence":0.7,
                "description":"Incomplete method chain — missing terminator"})
    return findings

def detect_overflow_risk(code):
    findings = []
    for i, line in enumerate(code.split("\n")):
        if "uint8_t" in line and any(k in line for k in ["total","sum","avg","count","acc"]):
            findings.append({"line_number":i+1,"line_text":line.strip(),
                "rule_tag":"overflow_risk","static_confidence":0.75,
                "description":"uint8_t accumulator will overflow for values > 255"})
        if re.search(r'\bint\b', line) and "sensor" in line.lower():
            findings.append({"line_number":i+1,"line_text":line.strip(),
                "rule_tag":"type_mismatch","static_confidence":0.6,
                "description":"Signed int for sensor value — consider uint16_t/uint32_t"})
    return findings

def detect_missing_volatile(code):
    findings = []
    for i, line in enumerate(code.split("\n")):
        if re.search(r'\b(bool|uint8_t|int)\b', line) and "=" in line and "volatile" not in line:
            if any(k in line for k in ["flag","ready","done","busy","irq","data_ready","isr"]):
                findings.append({"line_number":i+1,"line_text":line.strip(),
                    "rule_tag":"missing_volatile","static_confidence":0.85,
                    "description":"ISR-shared variable missing 'volatile'"})
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
                findings.append({"line_number":i+1,"line_text":line.strip(),
                    "rule_tag":"null_pointer","static_confidence":0.9,
                    "description":f"Pointer '{p}' dereferenced without allocation"})
    return findings

def detect_blocking_delay(code):
    findings, in_isr = [], False
    for i, line in enumerate(code.split("\n")):
        if re.search(r'\bISR\b|\bIRQ\b|interrupt|IRAM_ATTR|__irq', line, re.IGNORECASE):
            in_isr = True
        if in_isr and re.search(r'delay|HAL_Delay|sleep|busy_wait|while\s*\(', line):
            findings.append({"line_number":i+1,"line_text":line.strip(),
                "rule_tag":"blocking_in_isr","static_confidence":0.85,
                "description":"Blocking call inside interrupt context"})
        if "}" in line: in_isr = False
    return findings

def detect_bit_manipulation_error(code):
    findings = []
    for i, line in enumerate(code.split("\n")):
        if re.search(r'&\s*\d+\b', line) and "~" not in line:
            if any(k in line.lower() for k in ["clear","disable","off"]):
                findings.append({"line_number":i+1,"line_text":line.strip(),
                    "rule_tag":"bit_clear_error","static_confidence":0.8,
                    "description":"Use '& ~mask' not '& mask' to clear a bit"})
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

# ── LLM ─────────────────────────────────────
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
            messages=[{"role":"user","content":prompt}],
            temperature=0.1, max_tokens=1024
        )
        raw = response.choices[0].message.content
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if m: return json.loads(m.group())
    except Exception as e:
        print(f"[LLM] error: {e}")
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
        conf = round(0.6*float(validation.get("confidence",0)) + 0.4*best["static_confidence"], 3)
        llm_result = {
            "valid_bug": validation.get("valid_bug", False),
            "explanation": validation.get("explanation", ""),
            "corrected_code": validation.get("corrected_code", code),
            "confidence": conf
        }
    return {
        "static_findings": [
            {"line_number":f["line_number"],"line_text":f["line_text"],
             "rule_tag":f["rule_tag"],"description":f["description"],
             "confidence":f["static_confidence"],"source":"static"}
            for f in findings
        ],
        "llm_result": llm_result,
        "total_issues": len(findings),
        "llm_available": llm_result is not None
    }

# ── Routes ───────────────────────────────────
class AnalyzeRequest(BaseModel):
    code: str

@app.get("/health")
def health():
    return {"status":"ok","version":"2.0.0","llm":"groq"}

@app.post("/analyze")
def analyze(req: AnalyzeRequest):
    if not req.code.strip():
        raise HTTPException(status_code=400, detail="Code cannot be empty")
    return orchestrate(req.code)

@app.get("/ollama/status")
def ollama_status():
    key_set = bool(os.environ.get("GROQ_API_KEY",""))
    return {"available": key_set, "models": [GROQ_MODEL], "provider": "Groq"}

@app.get("/rules")
def rules():
    return [
        {"rule":"suspicious_method_name","confidence":0.80,"description":"Unknown RDI API method"},
        {"rule":"rdi_block_mismatch",     "confidence":0.90,"description":"RDI_BEGIN/RDI_END mismatch"},
        {"rule":"incomplete_chain",        "confidence":0.70,"description":"rdi. call without terminator"},
        {"rule":"overflow_risk",           "confidence":0.75,"description":"uint8_t accumulator overflow"},
        {"rule":"missing_volatile",        "confidence":0.85,"description":"ISR variable missing volatile"},
        {"rule":"null_pointer",            "confidence":0.90,"description":"Pointer used without allocation"},
        {"rule":"blocking_in_isr",         "confidence":0.85,"description":"Blocking delay in interrupt"},
        {"rule":"bit_clear_error",         "confidence":0.80,"description":"& mask should be & ~mask"},
    ]