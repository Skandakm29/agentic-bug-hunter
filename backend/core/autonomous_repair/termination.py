"""
Autonomous Repair Loop — Termination Policy  (Phase 3D.3)
==========================================================
``TerminationPolicy`` evaluates all configured stop conditions after each
repair iteration and returns the first matching ``TerminationReason``.

Seven conditions are checked in priority order:

1. ACCEPTED          — a candidate passed the acceptance threshold.
2. TIMEOUT           — wall-clock budget exceeded.
3. ITERATION_LIMIT   — max_iterations reached.
4. NO_CANDIDATES     — pool exhausted (no improvable candidates remain).
5. REPEATED_FAILURE  — N consecutive total-failure iterations.
6. CONVERGENCE       — score plateau detected.
7. MANUAL_STOP       — external stop signal raised.

The ``termination_policy`` setting controls which conditions are active:
- ``"success"``         → only ACCEPTED, TIMEOUT, MANUAL_STOP
- ``"iteration_limit"`` → only ITERATION_LIMIT, TIMEOUT, MANUAL_STOP
- ``"any"``             → all seven conditions (default)
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from backend.core.autonomous_repair.configuration import RepairConfiguration
from backend.core.autonomous_repair.repair_models import TerminationReason

logger = logging.getLogger("backend.autonomous_repair.termination")

# Sentinel value for "no termination yet"
_CONTINUE: TerminationReason = TerminationReason.UNKNOWN


class TerminationPolicy:
    """
    Evaluates all configured stop conditions for the repair loop.

    Parameters
    ----------
    config : ``RepairConfiguration`` with all stop-condition parameters.
    start_time : UNIX timestamp of session start (used for timeout check).
    """

    def __init__(
        self,
        config: Optional[RepairConfiguration] = None,
        start_time: Optional[float] = None,
    ) -> None:
        self._cfg = config or RepairConfiguration()
        self._start_time = start_time or time.time()
        self._manual_stop: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def request_stop(self) -> None:
        """Signal an external manual stop — will be picked up on next check."""
        self._manual_stop = True
        logger.info("TerminationPolicy: manual stop requested.")

    def should_terminate(
        self,
        iteration_index: int,
        accepted: bool,
        consecutive_failures: int,
        score_converged: bool,
        candidate_pool_size: int,
    ) -> Optional[TerminationReason]:
        """
        Evaluate all stop conditions and return the first matching reason,
        or ``None`` if the loop should continue.

        Parameters
        ----------
        iteration_index : Current (0-based) iteration index.
        accepted : True if a candidate has passed the acceptance threshold.
        consecutive_failures : Number of consecutive total-failure iterations.
        score_converged : True if ``ConvergenceDetector`` signalled a plateau.
        candidate_pool_size : Total number of candidates in the pool.
        """
        policy = self._cfg.termination_policy.lower()

        # ── Manual stop (always active regardless of policy) ──────────
        if self._manual_stop:
            return self._log_and_return(TerminationReason.MANUAL_STOP, iteration_index)

        # ── Timeout (always active) ───────────────────────────────────
        elapsed = time.time() - self._start_time
        if elapsed >= self._cfg.timeout_seconds:
            return self._log_and_return(TerminationReason.TIMEOUT, iteration_index)

        # ── Acceptance ────────────────────────────────────────────────
        if accepted:
            return self._log_and_return(TerminationReason.ACCEPTED, iteration_index)

        # ── Policy-gated conditions ───────────────────────────────────
        if policy in ("any", "iteration_limit"):
            # iteration_index is 0-based and this runs *after* the iteration
            # has completed, so the run has executed (iteration_index + 1)
            # iterations. Comparing the raw index against max_iterations
            # allowed one extra pass (6 iterations for max_iterations=5).
            if iteration_index + 1 >= self._cfg.max_iterations:
                return self._log_and_return(TerminationReason.ITERATION_LIMIT, iteration_index)

        if policy == "any":
            if candidate_pool_size == 0:
                return self._log_and_return(TerminationReason.NO_CANDIDATES, iteration_index)

            if consecutive_failures >= self._cfg.max_consecutive_failures:
                return self._log_and_return(TerminationReason.REPEATED_FAILURE, iteration_index)

            if score_converged:
                return self._log_and_return(TerminationReason.CONVERGENCE, iteration_index)

        # No condition triggered — continue the loop
        logger.debug(
            "TerminationPolicy: iteration %d — no termination condition met (policy=%s).",
            iteration_index,
            policy,
        )
        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _log_and_return(reason: TerminationReason, iteration: int) -> TerminationReason:
        logger.info(
            "TerminationPolicy: TERMINATE — reason=%s at iteration %d.",
            reason.value,
            iteration,
        )
        return reason
