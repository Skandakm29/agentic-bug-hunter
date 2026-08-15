from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from backend.core.config import settings
from backend.core.orchestrator import orchestrate_async

router = APIRouter()


class AnalyzeRequest(BaseModel):
    code: str


@router.get("/health")
def health():
    """Endpoint for monitoring API and service health."""
    return {"status": "ok", "version": "2.0.0", "llm": "groq"}


@router.post("/analyze")
async def analyze(req: AnalyzeRequest):
    """Analyze input C++ code for syntax errors and LLM semantic validation."""
    if not req.code.strip():
        raise HTTPException(status_code=400, detail="Code cannot be empty")
    return await orchestrate_async(req.code)


@router.get("/ollama/status")
def ollama_status():
    """Maintain backward compatibility for checking LLM health status."""
    key_set = bool(settings.GROQ_API_KEY)
    return {"available": key_set, "models": [settings.GROQ_MODEL], "provider": "Groq"}


from backend.core.analysis.rules.registry import RuleRegistry


@router.get("/rules")
def rules():
    """Retrieve the checklist of active static analysis rules dynamically."""
    rules_list = RuleRegistry.get_rules_for_language("cpp")
    return [
        {"rule": r.rule_id, "confidence": r.confidence, "description": r.name}
        for r in rules_list
    ]

