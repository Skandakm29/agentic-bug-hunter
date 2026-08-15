from backend.core.config import settings
from backend.core.analysis.engine import AnalysisEngine
from backend.core.analysis.schemas import NormalizedFinding
from backend.core.ai.providers.factory import LLMProviderFactory
from backend.core.ai.agents.validator import ValidatorAgent
from backend.core.ai.agents.fixer import FixerAgent
from backend.core.ai.agents.report_generator import ReportGeneratorAgent


async def orchestrate_async(code: str) -> dict:
    """
    Orchestrate the multi-agent C++ code analysis pipeline:
    1. Parse source C++ and run static platform rules using AnalysisEngine.
    2. Identify candidate finding for semantic validation.
    3. Invoke ValidatorAgent and FixerAgent for remediation details.
    4. Compile results through ReportGeneratorAgent.
    """
    # ── Step 1: Run static rule engine ───────────────────────
    engine = AnalysisEngine()
    findings = engine.analyze_plain(code, extension="cpp")

    # Pick highest-confidence finding
    if findings:
        best = max(findings, key=lambda x: x.static_confidence)
    else:
        best = NormalizedFinding(
            rule_id="semantic_review",
            line_number=None,
            line_text="General code review",
            severity="LOW",
            static_confidence=0.5,
            description="General review",
            evidence="",
            remediation="",
            source="static"
        )

    # ── Step 2: Initialize Provider & Agents ──────────────────
    provider = LLMProviderFactory.get_provider("groq")
    
    validator = ValidatorAgent(provider)
    fixer = FixerAgent(provider)
    report_gen = ReportGeneratorAgent(provider)

    # ── Step 3: Semantic check ──────────────────────────────
    best_dict = best.model_dump()
    validation = await validator.validate_finding(code, best_dict)

    # ── Step 4: Fix generation ──────────────────────────────
    correction = None
    if validation.valid_bug:
        correction = await fixer.generate_fix(code, best_dict, validation.explanation)

    combined_conf = round(
        settings.LLM_CONFIDENCE_WEIGHT * validation.confidence +
        settings.STATIC_CONFIDENCE_WEIGHT * float(best.static_confidence),
        3
    )

    # ── Step 5: Report compilation ──────────────────────────
    # Convert list of Pydantic objects to dicts for ReportGenerator
    findings_dicts = [f.model_dump() for f in findings]
    return report_gen.compile_report(
        findings=findings_dicts,
        validation_result=validation,
        correction=correction,
        combined_confidence=combined_conf
    )
