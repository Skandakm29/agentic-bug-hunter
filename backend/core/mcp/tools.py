import os
import csv
from pathlib import Path
from typing import Any
from backend.core.config import settings

from backend.core.orchestrator import orchestrate_async
from backend.core.analysis.rules.registry import RuleRegistry

# Global indicator for RAG status initialized by runner
rag_available = False
retriever = None


def set_rag_context(available: bool, active_retriever: Any = None):
    global rag_available, retriever
    rag_available = available
    retriever = active_retriever


async def mcp_analyze_code(code: str) -> dict:
    """Analyze code for bugs using static analysis + Groq LLM validation."""
    return await orchestrate_async(code)


async def mcp_batch_analyze(samples_csv_path: str) -> dict:
    """Run bug analysis on all code samples in a CSV file."""
    if not Path(samples_csv_path).exists():
        return {"error": f"File not found: {samples_csv_path}"}
    results = []
    try:
        with open(samples_csv_path, newline="", encoding="utf-8") as f:
            for i, row in enumerate(csv.DictReader(f)):
                code = row.get("code", "")
                if not code.strip():
                    continue
                result = await orchestrate_async(code)
                # orchestrate_async returns AnalysisReport.dict(), so
                # static_findings holds plain dicts, not Pydantic models.
                # Attribute access here raised AttributeError on every row.
                findings = result.get("static_findings") or []
                llm = result.get("llm_result")
                results.append({
                    "sample_index": i,
                    "code_preview": code[:80] + "..." if len(code) > 80 else code,
                    "total_issues": result.get("total_issues", 0),
                    "rule_tags": [
                        f.get("rule_tag") or f.get("rule_id") or "unknown"
                        for f in findings
                    ],
                    "llm_valid_bug": llm.get("valid_bug") if llm else None,
                    "llm_confidence": llm.get("confidence") if llm else None,
                })
    except Exception as e:
        return {"error": str(e)}

    total = len(results)
    bugs = sum(1 for r in results if r["total_issues"] > 0)
    return {
        "total_samples": total,
        "bugs_found": bugs,
        "clean_samples": total - bugs,
        "results": results,
        "summary_stats": {
            "bug_rate": f"{bugs/total*100:.1f}%" if total else "0%",
            "avg_issues": round(sum(r["total_issues"] for r in results) / total, 2) if total else 0
        }
    }


def mcp_get_static_rules() -> list:
    """Retrieve all active static rules and descriptions."""
    rules_list = RuleRegistry.get_rules_for_language("cpp")
    return [
        {"rule": r.rule_id, "confidence": r.confidence, "description": r.name}
        for r in rules_list
    ]


def mcp_get_server_status() -> dict:
    """Check components health status."""
    return {
        "static_engine": {"status": "online", "rules": len(RuleRegistry.get_rules_for_language("cpp"))},
        "llm": {"status": "online" if settings.GROQ_API_KEY else "no_api_key", "provider": "Groq", "model": settings.GROQ_MODEL},
        "rag_index": {"status": "online" if rag_available else "offline"},
        "mcp_server": {"status": "online", "port": int(os.environ.get("MCP_PORT", 8003)), "transport": "sse"}
    }


def mcp_search_documents(query: str) -> list:
    """Retrieve matched vector database records (RAG)."""
    if not rag_available or retriever is None:
        return [{"error": "RAG index not available"}]
    nodes = retriever.retrieve(query)
    return [{"text": n.get_text(), "score": n.get_score()} for n in nodes]
