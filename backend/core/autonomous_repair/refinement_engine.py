"""
Autonomous Repair Loop — Refinement Engine  (Phase 3D.3)
=========================================================
``RefinementEngine`` improves existing patch candidates instead of
regenerating from scratch.  It targets the specific failure identified by
``FeedbackEngine`` and ``ReasoningEngine``, applying a focused LLM call
with a much narrower prompt scope.

Design invariants
-----------------
- Provider-agnostic: depends only on ``BaseLLMProvider``.
- Preserves successful edits from the parent candidate.
- Always produces a new ``PatchCandidate`` — never mutates in-place.
- Falls back to the parent candidate if refinement fails.
- Supports 8 targeted refinement strategies (see ``RefinementStrategy``).
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from typing import Any, Dict, List, Optional

from backend.core.ai.providers.base import BaseLLMProvider
from backend.core.autonomous_repair.configuration import RepairConfiguration
from backend.core.autonomous_repair.repair_models import (
    RefinementStrategy,
    StructuredFeedback,
)
from backend.core.patch_generation.diff_generator import UnifiedDiffGenerator
from backend.core.patch_generation.patch_models import PatchCandidate

logger = logging.getLogger("backend.autonomous_repair.refinement_engine")

_REFINEMENT_SYSTEM = """You are a focused code repair specialist.
You are given a patch candidate that passed some validation checks but failed others.
Your task is to produce a MINIMAL targeted fix that addresses the specific failure reason.

Respond with JSON only:
{
  "patched_code": "<the complete fixed replacement code>",
  "reasoning": "<concise explanation of what was changed and why>",
  "confidence": <float 0.0–1.0>
}

Rules:
- Preserve ALL code that was working correctly.
- Only change what the failure reason indicates.
- Do NOT introduce new logic unrelated to the fix.
- Keep the same coding style."""

# Strategy-specific prompts appended to the user prompt
_STRATEGY_HINTS: Dict[RefinementStrategy, str] = {
    RefinementStrategy.MINIMAL_MODIFICATION:
        "Apply the smallest possible change that addresses the failure.",
    RefinementStrategy.ALTERNATIVE_API:
        "Replace the problematic API call with a safer alternative that achieves the same result.",
    RefinementStrategy.NULL_GUARD:
        "Add a null/nullptr check before the failing dereference.",
    RefinementStrategy.MEMORY_SAFETY:
        "Convert raw pointer management to RAII (unique_ptr/shared_ptr) or ensure proper delete.",
    RefinementStrategy.EXCEPTION_SAFE:
        "Wrap the critical section in a try/catch or use RAII to ensure exception safety.",
    RefinementStrategy.BOUNDARY_FIX:
        "Add bounds checking before the array/container access.",
    RefinementStrategy.OWNERSHIP_TRANSFER:
        "Clarify ownership semantics using std::move or ownership transfer patterns.",
    RefinementStrategy.THREAD_SAFE:
        "Protect the critical section with a mutex lock or use atomic operations.",
}


class RefinementEngine:
    """
    Targeted patch improvement engine that refines existing candidates.

    Accepts a parent ``PatchCandidate``, structured feedback, and a
    refinement strategy, then produces an improved ``PatchCandidate``
    that addresses the specific failure.
    """

    def __init__(
        self,
        provider: BaseLLMProvider,
        config: Optional[RepairConfiguration] = None,
    ) -> None:
        self._provider = provider
        self._cfg = config or RepairConfiguration()
        self._diff_gen = UnifiedDiffGenerator()

    async def refine(
        self,
        parent: PatchCandidate,
        feedback: StructuredFeedback,
        strategy: RefinementStrategy,
        file_path: str = "",
        preserve_elements: Optional[List[str]] = None,
        modify_elements: Optional[List[str]] = None,
    ) -> PatchCandidate:
        """
        Produce a refined ``PatchCandidate`` from a parent candidate.

        Parameters
        ----------
        parent : The candidate to refine.
        feedback : Structured feedback from the FeedbackEngine.
        strategy : Specific refinement approach to apply.
        file_path : Source file path (for diff generation).
        preserve_elements : Code elements that must NOT be changed.
        modify_elements : Code elements that MUST be changed.

        Returns
        -------
        A new ``PatchCandidate`` with the targeted refinement applied.
        If refinement fails, returns the parent candidate unchanged.
        """
        t0 = time.perf_counter()
        prompt = self._build_prompt(
            parent, feedback, strategy, file_path,
            preserve_elements or [], modify_elements or [],
        )

        try:
            raw = await self._provider.generate_completion_async(
                prompt=prompt,
                system_prompt=_REFINEMENT_SYSTEM,
                model=self._cfg.model_name,
                temperature=self._cfg.refinement_temperature,
                max_tokens=self._cfg.max_tokens,
            )
        except Exception as exc:
            logger.warning("RefinementEngine: LLM call failed — %s. Returning parent.", exc)
            return parent

        duration_ms = (time.perf_counter() - t0) * 1_000
        refined = self._parse_and_build(raw, parent, file_path, duration_ms)
        logger.info(
            "RefinementEngine: strategy=%s, confidence=%.2f, duration=%.1fms",
            strategy.value,
            refined.confidence,
            duration_ms,
        )
        return refined

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def _build_prompt(
        self,
        parent: PatchCandidate,
        feedback: StructuredFeedback,
        strategy: RefinementStrategy,
        file_path: str,
        preserve_elements: List[str],
        modify_elements: List[str],
    ) -> str:
        strategy_hint = _STRATEGY_HINTS.get(strategy, "Apply the most appropriate fix.")

        sections: List[str] = [
            f"## Refinement Task",
            f"Strategy: {strategy.value}",
            f"File: {file_path}",
            f"",
            f"### Specific Instruction",
            strategy_hint,
            f"",
            f"### Failure Reasons",
        ]
        for r in feedback.failure_reasons[:4]:
            sections.append(f"- [{r.severity.value}] {r.description}")
            if r.repair_hint:
                sections.append(f"  Fix: {r.repair_hint}")

        if preserve_elements:
            sections.append(f"\n### Must Preserve")
            for e in preserve_elements[:5]:
                sections.append(f"- {e}")

        if modify_elements:
            sections.append(f"\n### Must Modify")
            for e in modify_elements[:5]:
                sections.append(f"- {e}")

        sections += [
            f"",
            f"### Current Patched Code (to be refined)",
            f"```",
            parent.patched_code[:2000],
            f"```",
            f"",
            f"### Original Code (for reference)",
            f"```",
            parent.original_code[:1000],
            f"```",
            f"",
            f"Produce a refined version that fixes the above failures. Respond with JSON only.",
        ]
        return "\n".join(sections)

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _parse_and_build(
        self,
        raw: Optional[str],
        parent: PatchCandidate,
        file_path: str,
        duration_ms: float,
    ) -> PatchCandidate:
        data: Dict[str, Any] = {}
        if raw:
            try:
                data = json.loads(raw.strip())
            except json.JSONDecodeError:
                m = re.search(r"\{.*\}", raw, re.DOTALL)
                if m:
                    try:
                        data = json.loads(m.group())
                    except json.JSONDecodeError:
                        pass

        patched_code = str(data.get("patched_code", "")).strip()
        if not patched_code or patched_code == parent.original_code.strip():
            logger.warning("RefinementEngine: empty or identical refinement — returning parent.")
            return parent

        reasoning = str(data.get("reasoning", "Targeted refinement applied."))
        confidence = float(data.get("confidence", max(0.1, parent.confidence - 0.05)))
        confidence = max(0.0, min(1.0, confidence))

        # Generate diff (UnifiedDiffGenerator.generate builds the a/ and b/
        # headers itself and returns a ready-joined unified diff string).
        unified_diff = self._diff_gen.generate(
            parent.original_code,
            patched_code,
            file_path=file_path or None,
        )

        refined = PatchCandidate(
            candidate_id=str(uuid.uuid4())[:8],
            original_code=parent.original_code,
            patched_code=patched_code,
            unified_diff=unified_diff,
            reasoning=reasoning,
            explanation=parent.explanation,
            confidence=confidence,
            advantages=parent.advantages,
            disadvantages=parent.disadvantages,
            estimated_risk=parent.estimated_risk,
            preferred_rank=parent.preferred_rank,
            style_preserved=parent.style_preserved,
            metadata={
                **parent.metadata,
                "refined_from": parent.candidate_id,
                "refinement_duration_ms": round(duration_ms, 2),
            },
        )
        return refined
