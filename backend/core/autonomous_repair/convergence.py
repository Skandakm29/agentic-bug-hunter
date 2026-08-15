"""
Autonomous Repair Loop — Convergence Detector  (Phase 3D.3)
============================================================
``ConvergenceDetector`` monitors the score history across repair iterations
and signals when progress has plateaued, preventing infinite loops when no
further improvement is achievable.

Three detection strategies are supported:

ABSOLUTE_DELTA
    Converged if every score in the last ``window`` iterations changed by
    less than ``delta`` compared to its predecessor.

RELATIVE_DELTA
    Same as ABSOLUTE_DELTA but normalised by the baseline score.

WINDOW_VARIANCE
    Converged if the variance of the last ``window`` scores is below
    ``delta``.  More robust to non-monotonic score sequences.
"""
from __future__ import annotations

import logging
import statistics
from enum import Enum
from typing import List, Optional

from backend.core.autonomous_repair.configuration import RepairConfiguration

logger = logging.getLogger("backend.autonomous_repair.convergence")


class ConvergenceStrategy(str, Enum):
    """Algorithm used to determine score convergence."""

    ABSOLUTE_DELTA  = "absolute_delta"
    RELATIVE_DELTA  = "relative_delta"
    WINDOW_VARIANCE = "window_variance"


class ConvergenceDetector:
    """
    Detects when the repair loop score has plateaued and should stop.

    Parameters
    ----------
    config : ``RepairConfiguration`` providing ``convergence_window``
             and ``convergence_delta``.
    strategy : Detection algorithm (default: ABSOLUTE_DELTA).
    """

    def __init__(
        self,
        config: Optional[RepairConfiguration] = None,
        strategy: ConvergenceStrategy = ConvergenceStrategy.ABSOLUTE_DELTA,
    ) -> None:
        self._cfg = config or RepairConfiguration()
        self._strategy = strategy

    def check(self, scores: List[float]) -> bool:
        """
        Evaluate whether the score sequence has converged.

        Parameters
        ----------
        scores : List of composite scores, one per completed iteration
                 (oldest first, most recent last).

        Returns
        -------
        True if converged (loop should stop); False otherwise.
        """
        window = self._cfg.convergence_window
        delta = self._cfg.convergence_delta

        # Need at least window+1 data points to detect convergence
        if len(scores) < window + 1:
            return False

        recent = scores[-(window + 1):]  # last window+1 scores

        if self._strategy == ConvergenceStrategy.ABSOLUTE_DELTA:
            converged = self._check_absolute_delta(recent, delta)
        elif self._strategy == ConvergenceStrategy.RELATIVE_DELTA:
            converged = self._check_relative_delta(recent, delta)
        elif self._strategy == ConvergenceStrategy.WINDOW_VARIANCE:
            converged = self._check_window_variance(recent[-window:], delta)
        else:
            converged = self._check_absolute_delta(recent, delta)

        if converged:
            logger.info(
                "ConvergenceDetector: plateau detected using %s (window=%d, delta=%.4f). "
                "Recent scores: %s",
                self._strategy.value,
                window,
                delta,
                [round(s, 4) for s in recent],
            )
        return converged

    # ------------------------------------------------------------------
    # Strategy implementations
    # ------------------------------------------------------------------

    @staticmethod
    def _check_absolute_delta(scores: List[float], delta: float) -> bool:
        """All consecutive pairs differ by less than delta."""
        for i in range(1, len(scores)):
            if abs(scores[i] - scores[i - 1]) >= delta:
                return False
        return True

    @staticmethod
    def _check_relative_delta(scores: List[float], delta: float) -> bool:
        """All consecutive pairs differ by less than delta * baseline."""
        for i in range(1, len(scores)):
            baseline = abs(scores[i - 1]) if scores[i - 1] != 0.0 else 1.0
            if abs(scores[i] - scores[i - 1]) / baseline >= delta:
                return False
        return True

    @staticmethod
    def _check_window_variance(window_scores: List[float], delta: float) -> bool:
        """Variance of the window is below delta."""
        if len(window_scores) < 2:
            return False
        return statistics.variance(window_scores) < delta
