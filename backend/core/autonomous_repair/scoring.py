"""
Autonomous Repair Loop — Composite Repair Scorer  (Phase 3D.3)
===============================================================
``RepairScorer`` combines signals from the validation engine (3D.2),
the LLM-generated candidate metadata, and the ReviewerAgent to produce a
single composite score that drives the acceptance decision.

Scoring formula
---------------
    composite = (
        weight_validation    * validation_score   +
        weight_confidence    * candidate_confidence +
        weight_risk          * (1 − estimated_risk)  +
        weight_maintainability * maintainability_score +
        weight_simplicity    * simplicity_score     +
        weight_static_improvement * static_bonus
    )

All weights are sourced from ``RepairConfiguration`` so they can be tuned
without code changes.  The result is clamped to [0.0, 1.0].
"""
from __future__ import annotations

import logging
import math
from typing import Optional

from backend.core.autonomous_repair.configuration import RepairConfiguration
from backend.core.autonomous_repair.repair_models import CompositeScore

logger = logging.getLogger("backend.autonomous_repair.scoring")


class RepairScorer:
    """
    Composite repair candidate scorer.

    All weight parameters are injected from ``RepairConfiguration`` so
    the formula can be tuned without modifying source code.
    """

    def __init__(self, config: Optional[RepairConfiguration] = None) -> None:
        self._cfg = config or RepairConfiguration()

    def score(
        self,
        validation_score: float,
        candidate_confidence: float,
        estimated_risk: float,
        maintainability_score: float,
        lines_changed: int,
        new_bug_count: int,
    ) -> CompositeScore:
        """
        Compute a composite repair score from multi-dimensional signals.

        Parameters
        ----------
        validation_score : Overall score from ``ValidationEngine`` (0–1).
        candidate_confidence : LLM self-assessed confidence (0–1).
        estimated_risk : Candidate risk estimate (0–1, lower is better).
        maintainability_score : ReviewerAgent maintainability rating (0–1).
        lines_changed : Total lines modified by the patch.
        new_bug_count : Number of new static findings introduced.

        Returns
        -------
        ``CompositeScore`` with per-component breakdown and total.
        """
        cfg = self._cfg

        # ── Validation component ──────────────────────────────────────
        val_component = cfg.weight_validation * _clamp(validation_score)

        # ── Confidence component ──────────────────────────────────────
        conf_component = cfg.weight_confidence * _clamp(candidate_confidence)

        # ── Risk component (lower risk = higher contribution) ─────────
        risk_component = cfg.weight_risk * _clamp(1.0 - estimated_risk)

        # ── Maintainability component ─────────────────────────────────
        maint_component = cfg.weight_maintainability * _clamp(maintainability_score)

        # ── Simplicity component ──────────────────────────────────────
        #   Reward patches that touch fewer lines:
        #   0–5 lines → 1.0, 6–10 → 0.85, 11–20 → 0.70,
        #   21–50 → 0.50, 51–100 → 0.25, >100 → 0.05
        simplicity_raw = _simplicity_from_lines(lines_changed)
        simplicity_component = cfg.weight_simplicity * simplicity_raw

        # ── Static improvement component ──────────────────────────────
        #   Full bonus if no new bugs; reduced proportionally otherwise.
        if new_bug_count == 0:
            static_raw = 1.0
        else:
            static_raw = max(0.0, 1.0 - 0.25 * new_bug_count)
        static_component = cfg.weight_static_improvement * static_raw

        # ── Aggregate ─────────────────────────────────────────────────
        total = (
            val_component
            + conf_component
            + risk_component
            + maint_component
            + simplicity_component
            + static_component
        )
        total = _clamp(total)

        score = CompositeScore(
            total=round(total, 4),
            validation_component=round(val_component, 4),
            confidence_component=round(conf_component, 4),
            risk_component=round(risk_component, 4),
            maintainability_component=round(maint_component, 4),
            simplicity_component=round(simplicity_component, 4),
            static_improvement_component=round(static_component, 4),
        )

        logger.debug(
            "RepairScorer: total=%.4f (val=%.4f, conf=%.4f, risk=%.4f, "
            "maint=%.4f, simp=%.4f, static=%.4f)",
            score.total,
            score.validation_component,
            score.confidence_component,
            score.risk_component,
            score.maintainability_component,
            score.simplicity_component,
            score.static_improvement_component,
        )
        return score

    def passes_threshold(self, composite_score: CompositeScore) -> bool:
        """Return True if the composite score meets the acceptance threshold."""
        return composite_score.total >= self._cfg.acceptance_threshold


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp a float to [lo, hi]."""
    return max(lo, min(hi, value))


def _simplicity_from_lines(lines_changed: int) -> float:
    """Map lines-changed to a [0, 1] simplicity score."""
    if lines_changed <= 0:
        return 1.0
    if lines_changed <= 5:
        return 1.0
    if lines_changed <= 10:
        return 0.85
    if lines_changed <= 20:
        return 0.70
    if lines_changed <= 50:
        return 0.50
    if lines_changed <= 100:
        return 0.25
    # Exponential decay beyond 100 lines
    return max(0.05, 0.25 * math.exp(-0.005 * (lines_changed - 100)))
