"""
Autonomous Repair Loop — Exception Hierarchy  (Phase 3D.3)
===========================================================
All exceptions raised within ``backend.core.autonomous_repair`` descend
from ``RepairLoopError``.  This hierarchy allows callers to catch the
entire subsystem with a single ``except RepairLoopError`` clause, or to
handle specific failure modes precisely.

Design invariants
-----------------
- Every exception carries a human-readable ``message`` and an optional
  ``context`` dict for structured diagnostic data.
- ``RepairLoopError`` stores the iteration index at which the failure
  occurred (``-1`` when outside a loop iteration).
- All exceptions are picklable for future distributed-execution support.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class RepairLoopError(Exception):
    """
    Base exception for the Autonomous Multi-Agent Repair Loop.

    Parameters
    ----------
    message : Human-readable error description.
    iteration : Repair loop iteration index at failure time (−1 = pre-loop).
    context : Optional structured diagnostic data dict.
    """

    def __init__(
        self,
        message: str,
        iteration: int = -1,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.iteration = iteration
        self.context: Dict[str, Any] = context or {}

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"message={self.message!r}, "
            f"iteration={self.iteration}, "
            f"context={self.context!r})"
        )


# ---------------------------------------------------------------------------
# Agent errors
# ---------------------------------------------------------------------------


class AgentExecutionError(RepairLoopError):
    """
    Raised when a repair agent raises an unhandled exception during execution.

    Attributes
    ----------
    agent_role : String name of the failing agent role.
    original_error : The wrapped underlying exception.
    """

    def __init__(
        self,
        message: str,
        agent_role: str,
        original_error: Optional[Exception] = None,
        iteration: int = -1,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message, iteration=iteration, context=context)
        self.agent_role = agent_role
        self.original_error = original_error


class AgentTimeoutError(AgentExecutionError):
    """Raised when an agent exceeds its wall-clock execution budget."""


# ---------------------------------------------------------------------------
# Candidate / pool errors
# ---------------------------------------------------------------------------


class CandidatePoolExhaustedError(RepairLoopError):
    """
    Raised when the CandidatePool contains no improvable candidates and
    the loop cannot generate new ones under current constraints.
    """


class DuplicateCandidateError(RepairLoopError):
    """
    Raised when the loop attempts to add an already-existing patch candidate
    (same candidate_id) to the CandidatePool.
    """


# ---------------------------------------------------------------------------
# Loop control errors
# ---------------------------------------------------------------------------


class TerminationLimitError(RepairLoopError):
    """
    Raised when the loop terminates because a hard limit (maximum iterations
    or wall-clock timeout) has been reached without finding an accepted patch.
    """

    def __init__(
        self,
        message: str,
        reason: str = "unknown",
        iteration: int = -1,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message, iteration=iteration, context=context)
        self.reason = reason


class ConvergenceError(RepairLoopError):
    """
    Raised when the loop detects a stuck state: scores have plateaued across
    the configured convergence window and no alternative strategy is available.
    """


class NoValidCandidateError(RepairLoopError):
    """
    Raised when the session ends without any candidate meeting the acceptance
    threshold, after all termination conditions have been checked.
    """


# ---------------------------------------------------------------------------
# Intelligence layer errors
# ---------------------------------------------------------------------------


class FeedbackParseError(RepairLoopError):
    """
    Raised when ``FeedbackEngine`` cannot extract structured feedback from
    a ``ValidationReport`` due to unexpected format or missing required fields.
    """


class ReasoningFailureError(RepairLoopError):
    """
    Raised when ``ReasoningEngine`` receives a response from the LLM that
    cannot be parsed into a valid ``AgentDecision``.

    Attributes
    ----------
    raw_response : The unparseable LLM output string.
    """

    def __init__(
        self,
        message: str,
        raw_response: str = "",
        iteration: int = -1,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message, iteration=iteration, context=context)
        self.raw_response = raw_response


class RefinementError(RepairLoopError):
    """
    Raised when ``RefinementEngine`` cannot produce a valid refined candidate
    from the given parent candidate and structured feedback.
    """


# ---------------------------------------------------------------------------
# Orchestration errors
# ---------------------------------------------------------------------------


class OrchestrationError(RepairLoopError):
    """
    Raised by ``RepairOrchestrator`` for top-level session failures that are
    not attributable to a single agent or subsystem.
    """


class SessionConfigurationError(RepairLoopError):
    """
    Raised when ``RepairConfiguration`` contains invalid parameter
    combinations that would prevent a repair session from running.
    """


class WorkspaceSetupError(RepairLoopError):
    """
    Raised when the repair loop cannot prepare the isolated workspace
    required for patch validation.
    """
