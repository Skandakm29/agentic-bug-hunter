"""
Autonomous Repair Loop — Repair Memory  (Phase 3D.3)
=====================================================
``RepairMemory`` maintains a bounded, per-session sliding-window store of
repair iterations, agent decisions, and strategies.  It is the primary
source of historical context injected into agent prompts.

Design invariants
-----------------
- Bounded by ``max_entries`` (evicts oldest on overflow, FIFO).
- Thread-safe via reentrant lock.
- ``summarize()`` produces a compact narrative string for LLM context
  injection (≤ 1 000 characters by default).
"""
from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Deque, List, Set

from backend.core.autonomous_repair.repair_models import (
    AgentDecision,
    RepairIteration,
    RepairStrategy,
    StructuredFeedback,
)

logger = logging.getLogger("backend.autonomous_repair.memory")


class RepairMemory:
    """
    Bounded per-session memory for the repair loop.

    Stores complete ``RepairIteration`` records and exposes convenience
    accessors used by agents to reason about recent history.
    """

    def __init__(self, max_entries: int = 50) -> None:
        self._max_entries = max(5, max_entries)
        self._lock = threading.RLock()
        self._iterations: Deque[RepairIteration] = deque(maxlen=self._max_entries)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def add_iteration(self, iteration: RepairIteration) -> None:
        """
        Append a completed ``RepairIteration`` to memory.

        If memory is at capacity, the oldest entry is evicted automatically
        (FIFO — ``deque(maxlen=…)`` handles this transparently).
        """
        with self._lock:
            if len(self._iterations) == self._max_entries:
                logger.debug(
                    "RepairMemory: at capacity (%d), evicting oldest entry.",
                    self._max_entries,
                )
            self._iterations.append(iteration)
        logger.debug(
            "RepairMemory: stored iteration %d (strategy=%s, score=%.3f).",
            iteration.iteration_index,
            iteration.strategy.value,
            iteration.best_composite_score,
        )

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_all(self) -> List[RepairIteration]:
        """Return all stored iterations in chronological order."""
        with self._lock:
            return list(self._iterations)

    def get_recent(self, n: int) -> List[RepairIteration]:
        """Return the most recent *n* iterations (oldest-first within the slice)."""
        with self._lock:
            all_iters = list(self._iterations)
        return all_iters[-n:] if n < len(all_iters) else all_iters

    def get_latest(self) -> RepairIteration | None:
        """Return the most recently stored iteration, or None if empty."""
        with self._lock:
            return self._iterations[-1] if self._iterations else None

    def get_strategies_tried(self) -> Set[RepairStrategy]:
        """Return the set of all ``RepairStrategy`` values used so far."""
        with self._lock:
            return {it.strategy for it in self._iterations}

    def get_score_history(self) -> List[float]:
        """Return best composite score per iteration in chronological order."""
        with self._lock:
            return [it.best_composite_score for it in self._iterations]

    def get_all_agent_decisions(self) -> List[AgentDecision]:
        """Return every ``AgentDecision`` across all stored iterations."""
        decisions: List[AgentDecision] = []
        with self._lock:
            for it in self._iterations:
                decisions.extend(it.agent_decisions.values())
        return decisions

    def get_all_feedback(self) -> List[StructuredFeedback]:
        """Return every ``StructuredFeedback`` across all stored iterations."""
        with self._lock:
            return [it.feedback for it in self._iterations if it.feedback is not None]

    def consecutive_failures(self) -> int:
        """
        Count consecutive trailing iterations where ``improved=False``.

        Resets to 0 as soon as an improved iteration is found (from the end).
        """
        count = 0
        with self._lock:
            for it in reversed(self._iterations):
                if not it.improved:
                    count += 1
                else:
                    break
        return count

    @property
    def size(self) -> int:
        """Number of iteration records currently in memory."""
        with self._lock:
            return len(self._iterations)

    @property
    def is_empty(self) -> bool:
        with self._lock:
            return len(self._iterations) == 0

    # ------------------------------------------------------------------
    # Context generation
    # ------------------------------------------------------------------

    def summarize(self, max_chars: int = 1_000) -> str:
        """
        Produce a compact, LLM-friendly narrative of the repair history.

        The summary covers: total iterations, score progression, strategies
        tried, and the most recent failure reasons.  It is designed to fit
        within the configured character budget.
        """
        with self._lock:
            iters = list(self._iterations)

        if not iters:
            return "No repair iterations have been completed yet."

        lines: List[str] = []
        scores = [it.best_composite_score for it in iters]
        strategies = list({it.strategy.value for it in iters})

        lines.append(f"Repair history: {len(iters)} iteration(s) completed.")
        lines.append(f"Score progression: {[round(s, 3) for s in scores]}")
        lines.append(f"Strategies tried: {strategies}")

        # Most recent feedback summary
        recent = iters[-1]
        if recent.feedback:
            fb = recent.feedback
            reasons = [r.description for r in fb.failure_reasons[:3]]
            if reasons:
                lines.append(f"Latest failures: {'; '.join(reasons)}")
            if fb.primary_suggestion:
                lines.append(f"Primary suggestion: {fb.primary_suggestion}")

        # Preserve / modify hints from most recent planner
        planner_decision = recent.agent_decisions.get("planner")
        if planner_decision:
            if planner_decision.preserve_elements:
                lines.append(f"Preserve: {planner_decision.preserve_elements[:3]}")
            if planner_decision.modify_elements:
                lines.append(f"Modify: {planner_decision.modify_elements[:3]}")

        summary = "\n".join(lines)
        if len(summary) > max_chars:
            summary = summary[:max_chars - 3] + "..."
        return summary
