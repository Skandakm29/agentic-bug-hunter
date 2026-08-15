"""
Autonomous Repair Agents — Planner Agent  (Phase 3D.3)
=======================================================
``PlannerAgent`` synthesises all available information from the current
iteration — validation feedback, reasoning engine output, reviewer
assessment, memory history, and score progression — to select the optimal
``RepairStrategy`` for the next iteration.

Responsibilities
----------------
- Determine whether to refine, regenerate, switch approach, or escalate.
- Decide on the retry policy for the next cycle.
- Prioritise repair actions from the other agents.
- Produce the authoritative strategy recommendation.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional

from backend.core.autonomous_repair.agents.base_agent import BaseRepairAgent
from backend.core.autonomous_repair.repair_models import (
    AgentDecision,
    AgentRole,
    RepairStrategy,
    StructuredFeedback,
)

logger = logging.getLogger("backend.autonomous_repair.agents.planner")

_PLANNER_SYSTEM = """You are an autonomous repair loop planner.
Given the repair session state, decide the optimal strategy for the next iteration.
Respond with JSON only:
{
  "strategy": "<refine_existing|generate_new|switch_approach|escalate|stop>",
  "reasoning": "<justification for the chosen strategy>",
  "priority_actions": ["<ordered list of actions for next iteration>"],
  "preserve_elements": ["<must-preserve>"],
  "modify_elements": ["<must-change>"],
  "confidence": <float 0.0–1.0>
}"""


class PlannerAgent(BaseRepairAgent):
    """
    Repair agent that decides the strategy for the next repair iteration.
    """

    role = AgentRole.PLANNER

    async def _run(self, context: Dict[str, Any]) -> AgentDecision:
        """
        Parameters in ``context``
        -------------------------
        feedback : StructuredFeedback (optional)
        validator_decision : AgentDecision (optional)
        reviewer_decision : AgentDecision (optional)
        reasoning_decision : AgentDecision (optional)
        score_progression : List[float]
        strategies_tried : List[str]
        memory_summary : str
        iteration : int
        iteration_index : int (current)
        max_iterations : int
        consecutive_failures : int
        """
        t0 = time.perf_counter()
        iteration: int = context.get("iteration", 0)

        feedback: Optional[StructuredFeedback] = context.get("feedback")
        validator_decision: Optional[AgentDecision] = context.get("validator_decision")
        reviewer_decision: Optional[AgentDecision] = context.get("reviewer_decision")
        reasoning_decision: Optional[AgentDecision] = context.get("reasoning_decision")
        score_progression: List[float] = context.get("score_progression", [])
        strategies_tried: List[str] = context.get("strategies_tried", [])
        memory_summary: str = context.get("memory_summary", "")
        max_iterations: int = context.get("max_iterations", 5)
        consecutive_failures: int = context.get("consecutive_failures", 0)

        # Heuristic fast-path: avoid LLM call for obvious cases
        heuristic = self._heuristic_decision(
            feedback, score_progression, strategies_tried,
            iteration, max_iterations, consecutive_failures,
        )
        if heuristic:
            duration_ms = (time.perf_counter() - t0) * 1_000
            heuristic.duration_ms = round(duration_ms, 2)
            return heuristic

        # LLM-backed decision for ambiguous cases
        prompt = self._build_prompt(
            feedback, validator_decision, reviewer_decision, reasoning_decision,
            score_progression, strategies_tried, memory_summary, iteration, max_iterations,
        )
        try:
            raw = await self._provider.generate_completion_async(
                prompt=prompt,
                system_prompt=_PLANNER_SYSTEM,
                model=self._cfg.model_name,
                temperature=self._cfg.temperature,
                max_tokens=1024,
            )
        except Exception as exc:
            logger.warning("PlannerAgent: LLM call failed — %s. Using heuristic.", exc)
            raw = None

        duration_ms = (time.perf_counter() - t0) * 1_000
        return self._parse(raw, iteration, duration_ms)

    # ------------------------------------------------------------------
    # Heuristic fast-path
    # ------------------------------------------------------------------

    def _heuristic_decision(
        self,
        feedback: Optional[StructuredFeedback],
        score_progression: List[float],
        strategies_tried: List[str],
        iteration: int,
        max_iterations: int,
        consecutive_failures: int,
    ) -> Optional[AgentDecision]:
        """Return a strategy without LLM call for clear-cut cases."""

        # 1. Approaching limit — escalate temperature
        if iteration >= max_iterations - 1:
            return AgentDecision(
                agent_role=self.role,
                iteration=iteration,
                strategy=RepairStrategy.ESCALATE,
                reasoning="Approaching max_iterations — escalating to maximize final attempt.",
                confidence=0.8,
            )

        # 2. Too many consecutive failures — switch approach
        if consecutive_failures >= 2 and "switch_approach" not in strategies_tried:
            return AgentDecision(
                agent_role=self.role,
                iteration=iteration,
                strategy=RepairStrategy.SWITCH_APPROACH,
                reasoning=f"{consecutive_failures} consecutive failures — switching repair approach.",
                confidence=0.75,
            )

        # 3. Compilation failing → generate new
        if feedback and feedback.compilation_failed:
            if "generate_new" not in strategies_tried:
                return AgentDecision(
                    agent_role=self.role,
                    iteration=iteration,
                    strategy=RepairStrategy.GENERATE_NEW,
                    reasoning="Compilation failure on refined candidate — generating fresh candidate.",
                    confidence=0.7,
                )

        # 4. Score improving → refine existing
        if len(score_progression) >= 2:
            delta = score_progression[-1] - score_progression[-2]
            if delta > 0.02:
                return AgentDecision(
                    agent_role=self.role,
                    iteration=iteration,
                    strategy=RepairStrategy.REFINE_EXISTING,
                    reasoning=f"Score improving by {delta:.3f} — continue refining.",
                    confidence=0.8,
                )

        return None  # Defer to LLM

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def _build_prompt(
        self,
        feedback: Optional[StructuredFeedback],
        validator_decision: Optional[AgentDecision],
        reviewer_decision: Optional[AgentDecision],
        reasoning_decision: Optional[AgentDecision],
        score_progression: List[float],
        strategies_tried: List[str],
        memory_summary: str,
        iteration: int,
        max_iterations: int,
    ) -> str:
        lines = [
            f"## Repair Planning — Iteration {iteration}/{max_iterations}",
            f"Score progression: {[round(s, 3) for s in score_progression]}",
            f"Strategies tried: {strategies_tried}",
        ]
        if feedback:
            lines.append(f"\n## Current Feedback")
            lines.append(f"- Compilation failed: {feedback.compilation_failed}")
            lines.append(f"- Regression failed: {feedback.regression_failed}")
            lines.append(f"- Bug not removed: {feedback.bug_not_removed}")
            if feedback.primary_suggestion:
                lines.append(f"- Primary suggestion: {feedback.primary_suggestion}")
        if validator_decision:
            lines.append(f"\n## Validator Assessment\n{validator_decision.reasoning[:300]}")
        if reviewer_decision:
            maint = reviewer_decision.metadata.get("maintainability_score", 0.5)
            lines.append(f"\n## Reviewer Assessment\n{reviewer_decision.reasoning[:200]}")
            lines.append(f"Maintainability: {maint:.2f}")
        if reasoning_decision:
            lines.append(f"\n## Reasoning Engine Recommendation")
            lines.append(f"Strategy: {reasoning_decision.strategy.value if reasoning_decision.strategy else 'unknown'}")
            lines.append(f"Reasoning: {reasoning_decision.reasoning[:300]}")
        if memory_summary:
            lines.append(f"\n## History\n{memory_summary}")
        lines.append("\nDecide the optimal strategy for the next repair iteration.")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _parse(
        self,
        raw: Optional[str],
        iteration: int,
        duration_ms: float,
    ) -> AgentDecision:
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

        strategy_map = {
            "refine_existing": RepairStrategy.REFINE_EXISTING,
            "refine": RepairStrategy.REFINE_EXISTING,
            "generate_new": RepairStrategy.GENERATE_NEW,
            "generate": RepairStrategy.GENERATE_NEW,
            "switch_approach": RepairStrategy.SWITCH_APPROACH,
            "switch": RepairStrategy.SWITCH_APPROACH,
            "escalate": RepairStrategy.ESCALATE,
            "stop": RepairStrategy.STOP,
        }
        raw_strategy = str(data.get("strategy", "refine_existing")).lower()
        strategy = strategy_map.get(raw_strategy, RepairStrategy.REFINE_EXISTING)

        confidence = float(data.get("confidence", 0.6))
        confidence = max(0.0, min(1.0, confidence))

        return AgentDecision(
            agent_role=self.role,
            iteration=iteration,
            strategy=strategy,
            reasoning=str(data.get("reasoning", "LLM planning complete.")),
            confidence=confidence,
            recommended_actions=list(data.get("priority_actions", [])),
            preserve_elements=list(data.get("preserve_elements", [])),
            modify_elements=list(data.get("modify_elements", [])),
            duration_ms=round(duration_ms, 2),
        )
