"""
Patch Explainer Engine  (Phase 3D.1)
======================================
Generates structured ``PatchExplanation`` objects for every repair candidate
so that engineering reports contain human-readable, developer-facing
justification alongside the diff.

Explanation sources
-------------------
1. **LLM-provided reasoning** — If the candidate already has a complete
   ``explanation`` field populated by the parser (from the model's JSON
   output), it is used as-is.

2. **Rule-based synthesis** — Otherwise the engine composes the explanation
   from the repair guidance, root cause chain, and candidate metadata,
   filling in any field the model left blank.

Design invariants
-----------------
- This engine performs NO additional LLM calls. Patch generation already
  asks the model for a full explanation as part of its output schema, so a
  follow-up round-trip per candidate would add latency and cost for fields
  that can be derived deterministically.
- The synthesis path ALWAYS produces a valid ``PatchExplanation``; the
  engine NEVER returns None.
- Explanations are generated per-candidate independently so failures
  on one candidate do not affect others.
- ``provider`` is accepted for interface symmetry with the other pipeline
  components and is reserved for future use; it is not called today.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from backend.core.analysis.root_cause import RootCauseChain
from backend.core.analysis.schemas import NormalizedFinding
from backend.core.patch_generation.patch_models import (
    PatchCandidate,
    PatchExplanation,
    PatchGenerationConfig,
    RepairCategory,
)
from backend.core.patch_generation.repair_strategies import (
    RepairGuidanceRegistry,
)

logger = logging.getLogger("backend.patch_generation.patch_explainer")


class PatchExplainerEngine:
    """
    Generates ``PatchExplanation`` objects for patch candidates.

    Parameters
    ----------
    config   : Engine configuration.
    registry : Repair guidance registry used for rule-based synthesis.
    provider : Reserved for future use. Accepted for interface symmetry with
               the other pipeline components; this engine makes no LLM calls.
    """

    def __init__(
        self,
        config:   PatchGenerationConfig,
        registry: Optional[RepairGuidanceRegistry] = None,
        provider: Optional[object] = None,
    ) -> None:
        self._config   = config
        self._registry = registry or RepairGuidanceRegistry()
        self._provider = provider  # BaseLLMProvider | None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def explain_candidates(
        self,
        candidates:      List[PatchCandidate],
        finding:         NormalizedFinding,
        root_cause:      RootCauseChain,
        repair_category: RepairCategory,
    ) -> List[PatchCandidate]:
        """
        Populate the ``explanation`` field on each candidate in-place.

        Parameters
        ----------
        candidates      : List of ``PatchCandidate`` objects from the parser.
        finding         : The NormalizedFinding being fixed.
        root_cause      : Root cause chain for the finding.
        repair_category : Classified repair category.

        Returns
        -------
        The mutated candidates list (same objects, explanations filled in).
        """
        guidance = self._registry.get(repair_category)

        for candidate in candidates:
            # Use LLM-provided explanation if available and complete
            if self._is_complete(candidate.explanation):
                logger.debug(
                    "PatchExplainerEngine: candidate '%s' already has complete explanation.",
                    candidate.candidate_id,
                )
                continue

            # Build rule-based explanation
            explanation = self._build_fallback_explanation(
                candidate, finding, root_cause, repair_category, guidance
            )
            candidate.explanation = explanation

            logger.debug(
                "PatchExplainerEngine: generated fallback explanation for candidate '%s'.",
                candidate.candidate_id,
            )

        logger.info(
            "PatchExplainerEngine: explained %d candidate(s) for rule '%s'.",
            len(candidates),
            finding.rule_id,
        )
        return candidates

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_complete(explanation: Optional[PatchExplanation]) -> bool:
        """Return True if the explanation has all required fields populated."""
        if explanation is None:
            return False
        required = [
            explanation.why_bug_occurred,
            explanation.why_fix_works,
            explanation.safety_considerations,
        ]
        return all(
            f and f.strip() and f != "Not provided." and len(f) > 10
            for f in required
        )

    @staticmethod
    def _build_fallback_explanation(
        candidate:       PatchCandidate,
        finding:         NormalizedFinding,
        root_cause:      RootCauseChain,
        repair_category: RepairCategory,
        guidance:        object,
    ) -> PatchExplanation:
        """
        Synthesise a ``PatchExplanation`` from available metadata without
        an LLM call.
        """
        # If candidate already has a partial explanation, use it as base
        base = candidate.explanation

        # 1. Why the bug occurred
        why_bug = (
            (base.why_bug_occurred if base else "")
            or (root_cause.primary.hypothesis if root_cause.primary else "")
            or finding.description
        )

        # 2. Why fix works
        why_fix = (
            (base.why_fix_works if base else "")
            or candidate.reasoning
            or (
                f"The patch addresses the '{repair_category.value}' by applying "
                f"{guidance.repair_approach.splitlines()[0] if guidance.repair_approach else 'the recommended repair pattern'}."  # type: ignore[union-attr]
            )
        )

        # 3. Trade-offs
        adv  = "; ".join(candidate.advantages[:3]) or "Minimal change reduces regression risk."
        disadv = "; ".join(candidate.disadvantages[:2]) or "None identified."
        trade_offs = (base.trade_offs if base else "") or f"Advantages: {adv}. Disadvantages: {disadv}."

        # 4. Complexity impact
        orig_lines    = candidate.original_code.count('\n') + 1
        patched_lines = candidate.patched_code.count('\n') + 1
        delta         = patched_lines - orig_lines
        complexity_impact = (
            (base.complexity_impact if base else "")
            or (
                f"Net line delta: {delta:+d}. "
                f"{'Added guard conditions may marginally increase cyclomatic complexity.' if delta > 0 else 'Net reduction in code size.' if delta < 0 else 'No change in code size.'}"
            )
        )

        # 5. Safety considerations
        safety = (
            (base.safety_considerations if base else "")
            or getattr(guidance, 'safety_notes', None)
            or "Standard C++ safety practices apply."
        )

        # 6. Side effects
        side_effects = (
            (base.side_effects if base else "")
            or (
                "Cross-file changes may require recompilation of dependent units."
                if repair_category in {
                    RepairCategory.CONST_CORRECTNESS,
                    RepairCategory.INCORRECT_OWNERSHIP,
                }
                else "None identified for this repair category."
            )
        )

        # 7. Limitations
        limitations = (
            (base.limitations if base else "")
            or (
                "This patch addresses the immediate defect site. "
                "Callers should be audited for similar patterns."
            )
        )

        return PatchExplanation(
            why_bug_occurred      = why_bug.strip(),
            why_fix_works         = why_fix.strip(),
            trade_offs            = trade_offs.strip(),
            complexity_impact     = complexity_impact.strip(),
            safety_considerations = safety.strip(),
            side_effects          = side_effects.strip(),
            limitations           = limitations.strip(),
        )
