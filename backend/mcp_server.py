import os
import sys
from pathlib import Path
from fastmcp import FastMCP

# Inject parent directory into sys.path to allow importing from 'backend.core...'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.core.config import settings
from backend.core.mcp.registry import MCPToolRegistry
from backend.core.mcp.coordinator import MCPCoordinator
from backend.core.mcp.tools import (
    set_rag_context,
    mcp_analyze_code,
    mcp_batch_analyze,
    mcp_get_static_rules,
    mcp_get_server_status,
    mcp_search_documents
)

# ── Optional RAG Setup ───────────────────────
rag_available = False
retriever = None

try:
    from llama_index.embeddings.huggingface import HuggingFaceEmbedding
    from llama_index.core import StorageContext, load_index_from_storage, Settings as LlamaSettings
    from llama_index.core.retrievers import VectorIndexRetriever

    current_directory = os.getcwd()
    if os.path.basename(current_directory) == "backend":
        directory_path = os.path.join(".", "embedding_model")
        storage_path   = os.path.join(".", "storage")
    else:
        directory_path = os.path.join(".", "backend", "embedding_model")
        storage_path   = os.path.join(".", "backend", "storage")

    if Path(directory_path).is_dir() and Path(storage_path).is_dir():
        embed_model = HuggingFaceEmbedding(model_name=directory_path)
        LlamaSettings.embed_model = embed_model
        storage_context = StorageContext.from_defaults(persist_dir=storage_path)
        index = load_index_from_storage(storage_context=storage_context)
        retriever = VectorIndexRetriever(index=index, similarity_top_k=20)
        rag_available = True
        print("✅ RAG index loaded")
    else:
        print("⚠️  RAG storage not found — search_documents unavailable")
except Exception as e:
    print(f"⚠️  RAG setup skipped: {e}")

# Push RAG bindings to tools context
set_rag_context(rag_available, retriever)

# ── Register Capabilities to MCP registry ──
MCPToolRegistry.register("analyze_code", mcp_analyze_code, timeout=60.0, version="2.0.0")
MCPToolRegistry.register("batch_analyze", mcp_batch_analyze, timeout=300.0, version="2.0.0", cost="medium")
MCPToolRegistry.register("get_static_rules", mcp_get_static_rules, timeout=10.0, version="2.0.0")
MCPToolRegistry.register("get_server_status", mcp_get_server_status, timeout=10.0, version="2.0.0")
MCPToolRegistry.register("search_documents", mcp_search_documents, timeout=15.0, version="2.0.0")

# ── Launch FastMCP Server ─────────────────────
mcp = FastMCP("AgenticBugHunter")


@mcp.tool()
async def analyze_code(code: str) -> dict:
    """
    Analyze C++ / RDI code for bugs using static analysis + Groq LLM validation.
    Returns static findings, LLM explanation, corrected code, and confidence score.
    """
    return await MCPCoordinator.invoke_tool("analyze_code", code=code)


@mcp.tool()
async def batch_analyze(samples_csv_path: str) -> dict:
    """
    Run bug analysis on all code samples in a CSV file.
    CSV must have a 'code' column.
    """
    return await MCPCoordinator.invoke_tool("batch_analyze", samples_csv_path=samples_csv_path)


@mcp.tool()
async def get_static_rules() -> list:
    """Returns all available static analysis rules with descriptions and confidence levels."""
    return await MCPCoordinator.invoke_tool("get_static_rules")


@mcp.tool()
async def get_server_status() -> dict:
    """Returns current status of all components: static engine, Groq LLM, and RAG index."""
    return await MCPCoordinator.invoke_tool("get_server_status")


@mcp.tool()
async def search_documents(query: str) -> list:
    """Search the RDI API documentation using vector similarity (RAG)."""
    return await MCPCoordinator.invoke_tool("search_documents", query=query)


if __name__ == "__main__":
    print("=" * 50)
    print("  Agentic Bug Hunter — MCP Server")
    print(f"  Port     : {os.environ.get('MCP_PORT', 8003)}")
    print(f"  LLM      : Groq / {settings.GROQ_MODEL}")
    print(f"  RAG      : {'enabled' if rag_available else 'disabled'}")
    print(f"  API Key  : {'set ✅' if settings.GROQ_API_KEY else 'missing ❌'}")
    print("=" * 50)
    mcp.run(transport="sse")
