"""
Autonomous Repair Loop — Session Metrics  (Phase 3D.3)
=======================================================
``RepairMetricsCollector`` accumulates fine-grained execution statistics
throughout a repair session and emits a ``MetricsSnapshot`` on demand.

Design invariants
-----------------
- All counters are thread-safe via a reentrant lock.
- ``snapshot()`` is non-destructive — it can be called at any point.
- The collector is designed to be embedded in ``RepairSession.metrics_snapshot``
  and to be future-compatible with Prometheus label export.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import List, Set

from backend.core.autonomous_repair.repair_models import MetricsSnapshot, RepairStrategy

logger = logging.getLogger("backend.autonomous_repair.metrics")


class RepairMetricsCollector:
    """
    Session-level metrics accumulator for the autonomous repair loop.

    Records per-iteration timing, LLM call counts, agent invocation counts,
    and score progression.  Produces ``MetricsSnapshot`` for the engineering
    report and future Prometheus/OpenTelemetry export.
    """

    def __init__(self, session_id: str = "") -> None:
        self._session_id = session_id
        self._lock = threading.RLock()
        self._started_at: float = time.time()

        # Counters
        self._total_iterations: int = 0
        self._total_llm_calls: int = 0
        self._total_agent_invocations: int = 0
        self._total_candidates: int = 0
        self._accepted_candidates: int = 0
        self._rejected_candidates: int = 0
        self._improved_iterations: int = 0

        # Score tracking
        self._score_progression: List[float] = []
        self._best_score: float = 0.0

        # Strategy tracking
        self._strategies_used: Set[str] = set()

        # Per-iteration timing (ms)
        self._iteration_durations: List[float] = []

    # ------------------------------------------------------------------
    # Recording methods
    # ------------------------------------------------------------------

    def record_iteration_start(self, iteration_index: int) -> None:
        """Signal the start of a new repair iteration."""
        with self._lock:
            self._total_iterations += 1
        logger.debug("Metrics: iteration %d started.", iteration_index)

    def record_iteration_end(
        self,
        iteration_index: int,
        duration_ms: float,
        best_score: float,
        improved: bool,
        strategy: RepairStrategy,
    ) -> None:
        """Record results at the end of a repair iteration."""
        with self._lock:
            self._iteration_durations.append(duration_ms)
            self._score_progression.append(best_score)
            if best_score > self._best_score:
                self._best_score = best_score
            if improved:
                self._improved_iterations += 1
            self._strategies_used.add(strategy.value)
        logger.debug(
            "Metrics: iteration %d ended (score=%.3f, improved=%s, duration=%.1fms).",
            iteration_index,
            best_score,
            improved,
            duration_ms,
        )

    def record_llm_call(self, count: int = 1) -> None:
        """Increment the LLM call counter."""
        with self._lock:
            self._total_llm_calls += count

    def record_agent_invocation(self, count: int = 1) -> None:
        """Increment the agent invocation counter."""
        with self._lock:
            self._total_agent_invocations += count

    def record_candidate(self, accepted: bool = False, rejected: bool = False) -> None:
        """Record a new candidate produced or scored in this session."""
        with self._lock:
            self._total_candidates += 1
            if accepted:
                self._accepted_candidates += 1
            if rejected:
                self._rejected_candidates += 1

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def snapshot(self) -> MetricsSnapshot:
        """
        Produce a non-destructive ``MetricsSnapshot`` from the current
        accumulated state.
        """
        with self._lock:
            total_dur = (time.time() - self._started_at) * 1_000
            avg_iter = (
                total_dur / self._total_iterations
                if self._total_iterations > 0
                else 0.0
            )
            improvement_rate = (
                self._improved_iterations / self._total_iterations
                if self._total_iterations > 0
                else 0.0
            )
            return MetricsSnapshot(
                session_id=self._session_id,
                total_iterations=self._total_iterations,
                total_llm_calls=self._total_llm_calls,
                total_agent_invocations=self._total_agent_invocations,
                total_candidates=self._total_candidates,
                accepted_candidates=self._accepted_candidates,
                rejected_candidates=self._rejected_candidates,
                best_score_achieved=self._best_score,
                score_progression=list(self._score_progression),
                avg_iteration_ms=round(avg_iter, 2),
                total_duration_ms=round(total_dur, 2),
                improvement_rate=round(improvement_rate, 3),
                strategies_used=sorted(self._strategies_used),
            )

    def set_session_id(self, session_id: str) -> None:
        with self._lock:
            self._session_id = session_id
