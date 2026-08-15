"""
Autonomous Repair Loop — Reasoning Engine  (Phase 3D.3)
========================================================
``ReasoningEngine`` performs LLM-backed deep analysis of why a repair
iteration failed and what specific changes should be made in the next cycle.

Unlike ``FeedbackEngine`` (which is purely pattern-based), the reasoning
engine calls the LLM to produce nuanced, context-aware guidance: identifying
which function failed, what must be preserved, and which repair strategy
to switch to.

Design invariants
-----------------
- Provider-agnostic: depends only on ``BaseLLMProvider``.
- Graceful degradation: returns a default ``AgentDecision`` if the LLM
  returns unparseable output rather than raising.
- Structured JSON output: the LLM is prompted to return a strict JSON
  schema; fallback parsing handles partial responses.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

from backend.core.ai.providers.base import BaseLLMProvider
from backend.core.autonomous_repair.configuration import RepairConfiguration
from backend.core.autonomous_repair.repair_models import (
    AgentDecision,
    AgentRole,
    RepairStrategy,
    StructuredFeedback,
)
from backend.core.patch_generation.patch_models import StructuredPatch
from backend.core.patch_validation.validation_models import ValidationReport

logger = logging.getLogger("backend.autonomous_repair.reasoning_engine")

_REASONING_SYSTEM_PROMPT = """You are an expert software engineering advisor specialising in automated program repair.
Your task is to analyse a failed repair attempt and produce precise guidance for the next iteration.

You MUST respond with valid JSON matching this exact schema:
{
  "strategy": "<refine_existing|generate_new|switch_approach|escalate|stop>",
  "reasoning": "<concise explanation of why previous attempt failed>",
  "preserve_elements": ["<list of code elements / patterns that should NOT be changed>"],
  "modify_elements": ["<list of code elements / patterns that MUST be changed>"],
  "recommended_actions": ["<ordered list of concrete repair actions to take next>"],
  "confidence": <float 0.0–1.0>
}

Be specific about file paths, function names, and code patterns where possible.
Do not include prose outside the JSON object."""


class ReasoningEngine:
    """
    LLM-backed failure reasoning for the autonomous repair loop.

    Accepts structured feedback and the current patch state, calls the LLM
    to produce an ``AgentDecision`` specifying the recommended repair strategy
    and element-level guidance for the next iteration.
    """

    def __init__(
        self,
        provider: BaseLLMProvider,
        config: Optional[RepairConfiguration] = None,
    ) -> None:
        self._provider = provider
        self._cfg = config or RepairConfiguration()

    async def reason_about_failure(
        self,
        feedback: StructuredFeedback,
        patch: Optional[StructuredPatch],
        report: Optional[ValidationReport],
        memory_summary: str = "",
        iteration: int = 0,
    ) -> AgentDecision:
        """
        Analyse a failed repair iteration and produce strategic guidance.

        Parameters
        ----------
        feedback : ``StructuredFeedback`` from the FeedbackEngine.
        patch : The ``StructuredPatch`` that was validated (for code context).
        report : The raw ``ValidationReport`` for additional diagnostic detail.
        memory_summary : Compact history narrative from ``RepairMemory.summarize()``.
        iteration : Current loop iteration index.

        Returns
        -------
        ``AgentDecision`` with strategy, reasoning, and element-level guidance.
        """
        t0 = time.perf_counter()
        prompt = self._build_prompt(feedback, patch, report, memory_summary, iteration)

        try:
            raw = await self._provider.generate_completion_async(
                prompt=prompt,
                system_prompt=_REASONING_SYSTEM_PROMPT,
                model=self._cfg.model_name,
                temperature=self._cfg.reasoning_temperature,
                max_tokens=min(self._cfg.max_tokens, 2048),
            )
        except Exception as exc:
            logger.warning("ReasoningEngine: LLM call failed — %s. Returning fallback.", exc)
            return self._fallback_decision(iteration, reason=str(exc))

        duration_ms = (time.perf_counter() - t0) * 1_000
        decision = self._parse_response(raw, iteration, duration_ms)
        logger.info(
            "ReasoningEngine: iteration=%d, strategy=%s, confidence=%.2f, duration=%.1fms",
            iteration,
            decision.strategy.value if decision.strategy else "none",
            decision.confidence,
            duration_ms,
        )
        return decision

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def _build_prompt(
        self,
        feedback: StructuredFeedback,
        patch: Optional[StructuredPatch],
        report: Optional[ValidationReport],
        memory_summary: str,
        iteration: int,
    ) -> str:
        sections: List[str] = []

        sections.append(f"## Repair Iteration {iteration} — Failure Analysis\n")

        # Feedback summary
        sections.append("### Validation Feedback")
        sections.append(f"- Compilation failed: {feedback.compilation_failed}")
        sections.append(f"- Regression failed: {feedback.regression_failed}")
        sections.append(f"- Bug not removed: {feedback.bug_not_removed}")
        sections.append(f"- New bugs introduced: {feedback.new_bugs_introduced}")
        sections.append(f"- Score delta: {feedback.score_delta:+.4f}")

        if feedback.failure_reasons:
            sections.append("\n### Failure Reasons (ranked by severity)")
            for r in feedback.failure_reasons[:5]:
                sections.append(
                    f"- [{r.severity.value.upper()}] {r.category}: {r.description}\n"
                    f"  Hint: {r.repair_hint}"
                )

        # Patch context
        if patch and patch.file_patches:
            fp = patch.file_patches[0]
            best_idx = fp.best_candidate_index
            if 0 <= best_idx < len(fp.candidates):
                cand = fp.candidates[best_idx]
                sections.append("\n### Patch Candidate (best candidate from last iteration)")
                sections.append(f"File: {fp.file_path}")
                sections.append(f"Confidence: {cand.confidence}")
                diff_preview = cand.unified_diff[:800] if cand.unified_diff else "(no diff)"
                sections.append(f"Diff preview:\n```diff\n{diff_preview}\n```")

        # Validation errors
        if report and report.diagnostics.errors:
            sections.append("\n### Compiler / Validation Errors (first 5)")
            for err in report.diagnostics.errors[:5]:
                sections.append(f"- {err}")

        # Memory summary
        if memory_summary:
            sections.append(f"\n### Repair History Summary\n{memory_summary}")

        sections.append(
            "\n### Task\nBased on the above, specify the repair strategy and "
            "element-level guidance for the next iteration. Respond with JSON only."
        )
        return "\n".join(sections)

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _parse_response(
        self,
        raw: Optional[str],
        iteration: int,
        duration_ms: float,
    ) -> AgentDecision:
        if not raw:
            return self._fallback_decision(iteration, reason="Empty LLM response")

        data: Dict[str, Any] = {}

        # Try direct JSON parse first
        try:
            data = json.loads(raw.strip())
        except json.JSONDecodeError:
            # Fallback: extract JSON object via regex
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if m:
                try:
                    data = json.loads(m.group())
                except json.JSONDecodeError:
                    logger.warning("ReasoningEngine: could not parse LLM JSON from: %s", raw[:200])
                    return self._fallback_decision(iteration, reason="Unparseable JSON")

        # Map strategy string to enum
        raw_strategy = str(data.get("strategy", "refine_existing")).lower()
        strategy = _parse_strategy(raw_strategy)

        confidence = float(data.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))

        return AgentDecision(
            agent_role=AgentRole.VALIDATOR,      # ReasoningEngine speaks on behalf of Validator
            iteration=iteration,
            strategy=strategy,
            reasoning=str(data.get("reasoning", "")),
            confidence=confidence,
            recommended_actions=list(data.get("recommended_actions", [])),
            preserve_elements=list(data.get("preserve_elements", [])),
            modify_elements=list(data.get("modify_elements", [])),
            duration_ms=round(duration_ms, 2),
        )

    # ------------------------------------------------------------------
    # Fallback
    # ------------------------------------------------------------------

    @staticmethod
    def _fallback_decision(iteration: int, reason: str = "") -> AgentDecision:
        """Return a safe fallback decision when reasoning fails."""
        return AgentDecision(
            agent_role=AgentRole.VALIDATOR,
            iteration=iteration,
            strategy=RepairStrategy.REFINE_EXISTING,
            reasoning=f"Reasoning engine fallback: {reason}",
            confidence=0.3,
            recommended_actions=["Refine existing candidate targeting primary failure reason."],
        )


def _parse_strategy(raw: str) -> RepairStrategy:
    """Map a raw strategy string to a ``RepairStrategy`` enum value."""
    _map = {
        "refine_existing": RepairStrategy.REFINE_EXISTING,
        "refine": RepairStrategy.REFINE_EXISTING,
        "generate_new": RepairStrategy.GENERATE_NEW,
        "generate": RepairStrategy.GENERATE_NEW,
        "switch_approach": RepairStrategy.SWITCH_APPROACH,
        "switch": RepairStrategy.SWITCH_APPROACH,
        "escalate": RepairStrategy.ESCALATE,
        "stop": RepairStrategy.STOP,
    }
    return _map.get(raw, RepairStrategy.REFINE_EXISTING)
