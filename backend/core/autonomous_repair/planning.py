"""
Autonomous Repair Loop — Planning Engine  (Phase 3D.3)
=======================================================
``PlanningEngine`` determines the ``RepairStrategy`` and ``RefinementStrategy``
for each repair iteration.  It aggregates signals from all agents and the
session state to make a holistic, context-aware planning decision.

The engine uses a two-tier approach:
1. Rule-based decision tree for common cases (fast, deterministic).
2. PlannerAgent (LLM-backed) for ambiguous multi-signal situations.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from backend.core.autonomous_repair.configuration import RepairConfiguration
from backend.core.autonomous_repair.repair_models import (
    AgentDecision,
    RefinementStrategy,
    RepairStrategy,
    StructuredFeedback,
)

logger = logging.getLogger("backend.autonomous_repair.planning")


class PlanningEngine:
    """
    Determines the repair strategy for each iteration.

    Inputs
    ------
    - Current iteration index
    - CandidatePool score history
    - StructuredFeedback from FeedbackEngine
    - AgentDecisions from ValidatorAgent, ReviewerAgent, ReasoningEngine
    - Session memory (strategies already tried)
    """

    def __init__(self, config: Optional[RepairConfiguration] = None) -> None:
        self._cfg = config or RepairConfiguration()

    def decide_strategy(
        self,
        iteration_index: int,
        score_progression: List[float],
        feedback: Optional[StructuredFeedback],
        planner_decision: Optional[AgentDecision],
        strategies_tried: List[str],
        consecutive_failures: int,
    ) -> RepairStrategy:
        """
        Select the repair strategy for the next iteration.

        Parameters
        ----------
        iteration_index : Current (0-based) iteration index.
        score_progression : Best composite score per completed iteration.
        feedback : Structured feedback from the last iteration.
        planner_decision : AgentDecision from PlannerAgent (may be None).
        strategies_tried : List of strategy values already used.
        consecutive_failures : Trailing iterations with no improvement.

        Returns
        -------
        The selected ``RepairStrategy``.
        """
        # 1. PlannerAgent has the highest authority when available
        if planner_decision and planner_decision.strategy is not None:
            logger.info(
                "PlanningEngine: deferring to PlannerAgent → %s",
                planner_decision.strategy.value,
            )
            return planner_decision.strategy

        # 2. Rule-based fallback
        strategy = self._rule_based(
            iteration_index, score_progression, feedback,
            strategies_tried, consecutive_failures,
        )
        logger.info("PlanningEngine: rule-based decision → %s", strategy.value)
        return strategy

    def select_refinement_strategy(
        self,
        feedback: Optional[StructuredFeedback],
    ) -> RefinementStrategy:
        """
        Map the dominant failure reason to a specific RefinementStrategy.
        """
        if not feedback or not feedback.failure_reasons:
            return RefinementStrategy.MINIMAL_MODIFICATION

        top = feedback.failure_reasons[0]
        category = top.category.lower()

        if "compilation" in category or "syntax" in category:
            return RefinementStrategy.MINIMAL_MODIFICATION
        if "null" in category or "dereference" in category:
            return RefinementStrategy.NULL_GUARD
        if "memory" in category or "leak" in category:
            return RefinementStrategy.MEMORY_SAFETY
        if "exception" in category:
            return RefinementStrategy.EXCEPTION_SAFE
        if "boundary" in category or "overflow" in category or "bounds" in category:
            return RefinementStrategy.BOUNDARY_FIX
        if "ownership" in category or "double_free" in category:
            return RefinementStrategy.OWNERSHIP_TRANSFER
        if "thread" in category or "race" in category:
            return RefinementStrategy.THREAD_SAFE
        if "api" in category:
            return RefinementStrategy.ALTERNATIVE_API

        return RefinementStrategy.MINIMAL_MODIFICATION

    # ------------------------------------------------------------------
    # Rule-based decision tree
    # ------------------------------------------------------------------

    def _rule_based(
        self,
        iteration_index: int,
        score_progression: List[float],
        feedback: Optional[StructuredFeedback],
        strategies_tried: List[str],
        consecutive_failures: int,
    ) -> RepairStrategy:
        cfg = self._cfg

        # Escalate on last possible iteration
        if iteration_index >= cfg.max_iterations - 1:
            return RepairStrategy.ESCALATE

        # Compilation failure → switch from refine to generate_new
        if feedback and feedback.compilation_failed:
            if RepairStrategy.GENERATE_NEW.value not in strategies_tried:
                return RepairStrategy.GENERATE_NEW

        # Too many consecutive failures → switch approach
        if consecutive_failures >= 2:
            if RepairStrategy.SWITCH_APPROACH.value not in strategies_tried:
                return RepairStrategy.SWITCH_APPROACH
            return RepairStrategy.GENERATE_NEW

        # Score improving → keep refining
        if len(score_progression) >= 2:
            delta = score_progression[-1] - score_progression[-2]
            if delta > cfg.convergence_delta:
                return RepairStrategy.REFINE_EXISTING

        # First iteration → generate new
        if iteration_index == 0:
            return RepairStrategy.GENERATE_NEW

        # Default: prefer refinement if configured
        if cfg.prefer_refinement:
            return RepairStrategy.REFINE_EXISTING
        return RepairStrategy.GENERATE_NEW
