"""
orchestrator.py — Pipeline Coordinator

SOLID Principles Applied:
  S → Orchestrator has ONE job: coordinate the pipeline
      It doesn't implement rules, call APIs, or manage DB
      It delegates to StaticEngine, LLMValidator, CacheManager
  D → All dependencies injected — not created internally
      Makes testing easy (mock any component)
      Makes swapping components easy
"""

from static_engine import StaticEngine, Finding
from llm_validator import LLMValidator
from cache_manager import CacheManager


class Orchestrator:
    """
    Coordinates the full analysis pipeline.

    Pipeline:
      1. Cache check (PostgreSQL) — skip if seen before
      2. Static engine — 9 rules, <10ms, deterministic
      3. Agentic decision — pick highest confidence finding
      4. LLM validation — semantic analysis with focused context
      5. Confidence merge — 0.6×LLM + 0.4×static
      6. Cache save — store result for future requests
      7. Return structured result

    What makes this AGENTIC:
      The orchestrator DECIDES which finding to escalate (step 3).
      The next action depends on what the previous step found.
      This is not a fixed sequence — it's decision-based routing.
      If confidence < threshold (future), it will retry with more context.

    Dependency Inversion:
      Orchestrator depends on abstractions (StaticEngine, LLMValidator,
      CacheManager) injected via constructor — not created internally.
      This makes every component independently testable and swappable.
    """

    # Confidence weights — empirically tuned on sample dataset
    LLM_WEIGHT    = 0.6   # LLM gets more weight: understands context
    STATIC_WEIGHT = 0.4   # Static gets weight: deterministic when it fires

    def __init__(
        self,
        static_engine:  StaticEngine,
        llm_validator:  LLMValidator,
        cache_manager:  CacheManager
    ):
        self.static_engine  = static_engine
        self.llm_validator  = llm_validator
        self.cache_manager  = cache_manager

    def analyze(self, code: str) -> dict:
        """
        Run the full analysis pipeline and return structured result.
        """

        # ── Step 1: Cache check ───────────────────────────────────────
        cached = self.cache_manager.get(code)
        if cached:
            print("[CACHE] Hit — returning cached result")
            return cached

        # ── Step 2: Static analysis ───────────────────────────────────
        findings = self.static_engine.analyze(code)

        # ── Step 3: Agentic decision — pick best finding ──────────────
        # This is the agentic part: the system DECIDES which finding
        # is most worth deeper LLM investigation instead of sending all
        if findings:
            best = max(findings, key=lambda f: f.static_confidence)
        else:
            # No static findings — still escalate to LLM for general review
            # System doesn't give up just because regex found nothing
            best = Finding(
                line_number=None,
                line_text="General code review",
                rule_tag="semantic_review",
                static_confidence=0.5,
                description="No specific pattern matched — general review"
            )

        # ── Step 4: LLM validation ────────────────────────────────────
        validation = self.llm_validator.validate(code, best)

        # ── Step 5: Merge confidence scores ──────────────────────────
        llm_result = None
        if validation:
            llm_conf    = float(validation.get("confidence", 0))
            static_conf = best.static_confidence
            final_conf  = round(
                self.LLM_WEIGHT * llm_conf +
                self.STATIC_WEIGHT * static_conf,
                3
            )
            llm_result = {
                "valid_bug":      validation.get("valid_bug", False),
                "explanation":    validation.get("explanation", ""),
                "corrected_code": validation.get("corrected_code", code),
                "confidence":     final_conf
            }

        # ── Step 6: Build result ──────────────────────────────────────
        result = {
            "static_findings": [f.to_dict() for f in findings],
            "llm_result":      llm_result,
            "total_issues":    len(findings),
            "llm_available":   llm_result is not None
        }

        # ── Step 7: Save to cache ─────────────────────────────────────
        self.cache_manager.save(code, result)

        return result
