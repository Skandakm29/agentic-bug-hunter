"""
mcp_server.py — FastMCP server with custom REST endpoints for direct HTTP calls.
Exposes /detect endpoint for batch_runner + MCP tools for AI agents.
"""

import math
import os
import json
import sys
from pathlib import Path
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.applications import Starlette

# ── Optional RAG ─────────────────────────────────────────────────────────────
retriever = None
current_directory = os.getcwd()
is_server_dir = os.path.basename(current_directory) == "server"
directory_path = os.path.join(".", "embedding_model") if is_server_dir else os.path.join(".", "server", "embedding_model")
storage_path = os.path.join(".", "storage") if is_server_dir else os.path.join(".", "server", "storage")

try:
    from llama_index.embeddings.huggingface import HuggingFaceEmbedding
    from llama_index.core import StorageContext, load_index_from_storage, Settings
    from llama_index.core.retrievers import VectorIndexRetriever
    if Path(directory_path).is_dir() and Path(storage_path).is_dir():
        embed_model = HuggingFaceEmbedding(model_name=directory_path)
        Settings.embed_model = embed_model
        storage_context = StorageContext.from_defaults(persist_dir=storage_path)
        index = load_index_from_storage(storage_context=storage_context)
        retriever = VectorIndexRetriever(index=index, similarity_top_k=20)
        print(f"[Server] ✅ RAG initialized")
    else:
        print(f"[Server] ⚠️  RAG storage not found — running without RAG")
except ImportError:
    print("[Server] ⚠️  llama_index not installed — running without RAG")
except Exception as e:
    print(f"[Server] ⚠️  RAG init failed: {e}")

# ── Import local agents ───────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from static_engine import analyze_code
from validator_agent import validate_issue, llm_fallback_analyze, parse_llm_json
from orchestrator import analyze_logic

# ── FastMCP setup ─────────────────────────────────────────────────────────────
mcp = FastMCP("ABH_Server")


# ── MCP Tools (for AI agents like Claude Desktop) ────────────────────────────

@mcp.tool()
def detect_bugs(code: str, context: str = "") -> dict:
    """Full agentic bug detection: static analysis + LLM validation."""
    print(f"[MCP Tool] detect_bugs called")
    bug_line, explanation = analyze_logic(context, code)
    return {"bug_line": bug_line, "explanation": explanation, "has_bug": bug_line is not None}

@mcp.tool()
def static_analyze(code: str) -> dict:
    """Fast static-only analysis — no LLM."""
    findings = analyze_code(code)
    return {"findings": findings, "count": len(findings)}

@mcp.tool()
def search_documents(query: str) -> list:
    """RAG search over embedded system docs."""
    if not retriever:
        return [{"text": "RAG not available.", "score": 0.0}]
    nodes = retriever.retrieve(query)
    return [{"text": n.get_text(), "score": n.get_score()} for n in nodes]

@mcp.tool()
def list_files_and_folders() -> list:
    """List files in current directory."""
    return os.listdir(".")


# ── Custom REST endpoints (for batch_runner HTTP calls) ───────────────────────

async def rest_detect_bugs(request: Request):
    """POST /detect — direct REST endpoint for batch_runner."""
    try:
        body = await request.json()
        code = body.get("code", "")
        context = body.get("context", "")
        if not code:
            return JSONResponse({"error": "code field required"}, status_code=400)
        print(f"[REST] /detect called, code length={len(code)}")
        bug_line, explanation = analyze_logic(context, code)
        return JSONResponse({
            "bug_line": bug_line,
            "explanation": explanation,
            "has_bug": bug_line is not None
        })
    except Exception as e:
        return JSONResponse({"error": str(e), "bug_line": None, "explanation": str(e)}, status_code=500)


async def rest_static_analyze(request: Request):
    """POST /static — static analysis only."""
    try:
        body = await request.json()
        code = body.get("code", "")
        findings = analyze_code(code)
        return JSONResponse({"findings": findings, "count": len(findings)})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def health_check(request: Request):
    """GET / — health check."""
    return JSONResponse({"status": "ok", "server": "ABH_Server", "version": "1.0"})


# ── Mount REST routes onto the MCP app ────────────────────────────────────────

import uvicorn
from starlette.routing import Route, Mount
from starlette.applications import Starlette

# Get the underlying MCP ASGI app
mcp_app = mcp.http_app(path="/mcp")

# Build combined app with REST routes + MCP
routes = [
    Route("/", health_check, methods=["GET"]),
    Route("/detect", rest_detect_bugs, methods=["POST"]),
    Route("/static", rest_static_analyze, methods=["POST"]),
    Mount("/", app=mcp_app),
]

app = Starlette(routes=routes)


if __name__ == "__main__":
    print("=" * 50)
    print("  Agentic Bug Hunter — MCP Server")
    print("  http://127.0.0.1:8003")
    print("  REST endpoints:")# Terminal 1 — install uvicorn then restart server
    print("    POST /detect  — full hybrid analysis")
    print("    POST /static  — static analysis only")
    print("    GET  /        — health check")
    print("  MCP endpoint:")
    print("    /mcp          — for AI agents")
    print("=" * 50)
    uvicorn.run(app, host="127.0.0.1", port=8003)