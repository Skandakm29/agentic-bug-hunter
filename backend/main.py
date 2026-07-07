"""
main.py — FastAPI Application Entry Point

SOLID Principles Applied:
  S → main.py has ONE job: HTTP routing
      No business logic here
      All logic delegated to injected components

  D → All dependencies created once at startup
      Injected into Orchestrator
      Routes just call orchestrator.analyze()
"""

from dotenv import load_dotenv
load_dotenv()  # MUST be first — loads env vars before anything reads them

import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from groq import Groq

from static_engine  import StaticEngine
from llm_validator  import LLMValidator
from cache_manager  import CacheManager
from orchestrator   import Orchestrator

# ── App setup ─────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Agentic Bug Hunter API",
    version="3.0.0",
    description="Hybrid AI bug detection — static rules + LLM + PostgreSQL cache + RAG"
)

# ── CORS ──────────────────────────────────────────────────────────────────────
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:3000")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL, "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Dependency injection — created ONCE at startup ────────────────────────────
# This is the Dependency Inversion Principle in action:
# Each component is created here and injected into the next
# Orchestrator doesn't know HOW to call Groq — it just uses LLMValidator
# LLMValidator doesn't know about caching — that's CacheManager's job

groq_client = Groq(api_key=os.environ.get("GROQ_API_KEY", ""))
GROQ_MODEL  = "llama-3.1-8b-instant"

static_engine  = StaticEngine()
llm_validator  = LLMValidator(groq_client, GROQ_MODEL)
cache_manager  = CacheManager(os.environ.get("DATABASE_URL", ""))
orchestrator   = Orchestrator(static_engine, llm_validator, cache_manager)

# ── Request schema ────────────────────────────────────────────────────────────
class AnalyzeRequest(BaseModel):
    code: str
    # Pydantic validates:
    #   "code" must exist → else 422 Unprocessable Entity
    #   "code" must be str → else 422
    # We manually check content below (Pydantic checks TYPE not VALUE)


# ── Routes — HTTP layer only, no business logic ───────────────────────────────

@app.get("/health")
def health():
    """Server and LLM availability check. Used by frontend status indicator."""
    return {
        "status":   "ok",
        "version":  "3.0.0",
        "llm":      GROQ_MODEL,
        "provider": "Groq"
    }


@app.post("/analyze")
def analyze(req: AnalyzeRequest):
    """
    Main analysis endpoint.

    Status codes:
      200 → analysis complete (bugs found is NOT an error)
      400 → code field is empty or whitespace
      422 → Pydantic validation failed (missing field or wrong type)
      500 → unexpected server error
    """
    if not req.code.strip():
        raise HTTPException(status_code=400, detail="Code cannot be empty")

    return orchestrator.analyze(req.code)


@app.get("/rules")
def rules():
    """
    Returns metadata about all registered static rules.
    Used by MCP server's get_static_rules tool.
    Allows API consumers to discover what bugs are detected
    without reading source code.
    """
    return static_engine.get_rules_metadata()


@app.get("/ollama/status")
def llm_status():
    """
    Legacy endpoint — originally checked local Ollama.
    Now checks Groq API key availability.
    Kept for backwards compatibility with frontend health check.
    """
    key_set = bool(os.environ.get("GROQ_API_KEY", ""))
    return {
        "available": key_set,
        "models":    [GROQ_MODEL],
        "provider":  "Groq"
    }


# ── Mount MCP Server ──────────────────────────────────────────────────────────
# MCP = Model Context Protocol — exposes the same pipeline as tools
# that AI assistants (Claude, Cursor) can call directly
# try/except ensures FastAPI works even if MCP has issues
try:
    from mcp_server import mcp
    mcp_app = mcp.http_app()
    app.mount("/mcp", mcp_app)
    app.router.lifespan_context = mcp_app.lifespan
    print("✅ MCP server mounted at /mcp")
except Exception as e:
    print(f"⚠️  MCP server not mounted: {e}")
