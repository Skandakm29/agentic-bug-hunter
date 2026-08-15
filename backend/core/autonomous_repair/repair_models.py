"""
Autonomous Repair Loop — Domain Models  (Phase 3D.3)
=====================================================
All Pydantic data models that form the public data contract of the
Autonomous Multi-Agent Repair Loop.  Consumers of ``RepairOrchestrator``
use these models exclusively; no internal implementation types are exposed.

Design invariants
-----------------
- All models use ``ConfigDict(frozen=False)`` to allow in-place enrichment.
- UUIDs are auto-generated for all identity fields.
- Timestamp fields use UNIX epoch floats for timezone independence.
- The ``metadata`` dict on composite objects is an open-extension point.
"""
from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class AgentRole(str, Enum):
    """Canonical roles for repair agents."""

    VALIDATOR       = "validator"
    PATCH_GENERATOR = "patch_generator"
    REVIEWER        = "reviewer"
    PLANNER         = "planner"
    REPORT          = "report"


class RepairStrategy(str, Enum):
    """
    Strategy the PlanningEngine selects for the next repair iteration.

    Each strategy maps to a concrete execution path in ``RepairLoop``.
    """

    REFINE_EXISTING   = "refine_existing"    # Improve the best current candidate
    GENERATE_NEW      = "generate_new"       # Discard and generate from scratch
    SWITCH_APPROACH   = "switch_approach"    # Try a different repair category strategy
    ESCALATE          = "escalate"           # Raise temperature / use a stronger model
    STOP              = "stop"               # Terminate the loop immediately


class TerminationReason(str, Enum):
    """Reason for loop termination."""

    ACCEPTED          = "accepted"           # Candidate passed acceptance threshold
    ITERATION_LIMIT   = "iteration_limit"    # max_iterations reached
    TIMEOUT           = "timeout"            # Wall-clock timeout expired
    CONVERGENCE       = "convergence"        # Score plateau detected
    NO_CANDIDATES     = "no_candidates"      # Pool exhausted
    REPEATED_FAILURE  = "repeated_failure"   # Consecutive total-failure iterations
    MANUAL_STOP       = "manual_stop"        # External stop signal
    UNKNOWN           = "unknown"


class FailureSeverity(str, Enum):
    """Severity of a repair failure."""

    CRITICAL  = "critical"    # Compilation / syntax failure
    HIGH      = "high"        # Regression failure / new bug introduced
    MEDIUM    = "medium"      # Score below threshold
    LOW       = "low"         # Style or confidence issue


class RefinementStrategy(str, Enum):
    """Specific refinement approach used by ``RefinementEngine``."""

    MINIMAL_MODIFICATION = "minimal_modification"
    ALTERNATIVE_API      = "alternative_api"
    NULL_GUARD           = "null_guard"
    MEMORY_SAFETY        = "memory_safety"
    EXCEPTION_SAFE       = "exception_safe"
    BOUNDARY_FIX         = "boundary_fix"
    OWNERSHIP_TRANSFER   = "ownership_transfer"
    THREAD_SAFE          = "thread_safe"


class AuditEventType(str, Enum):
    """Types of events recorded in the AuditTrail."""

    SESSION_START       = "session_start"
    SESSION_END         = "session_end"
    ITERATION_START     = "iteration_start"
    ITERATION_END       = "iteration_end"
    AGENT_INVOKED       = "agent_invoked"
    AGENT_DECISION      = "agent_decision"
    CANDIDATE_GENERATED = "candidate_generated"
    CANDIDATE_REFINED   = "candidate_refined"
    VALIDATION_STARTED  = "validation_started"
    VALIDATION_COMPLETE = "validation_complete"
    CANDIDATE_ACCEPTED  = "candidate_accepted"
    CANDIDATE_REJECTED  = "candidate_rejected"
    STRATEGY_SELECTED   = "strategy_selected"
    CONVERGENCE_CHECK   = "convergence_check"
    TERMINATION_CHECK   = "termination_check"
    LOOP_TERMINATED     = "loop_terminated"
    AGENT_ERROR         = "agent_error"


# ---------------------------------------------------------------------------
# Structured Feedback
# ---------------------------------------------------------------------------


class FailureReason(BaseModel):
    """A single parsed failure reason extracted from a validation report."""

    model_config = ConfigDict(frozen=False)

    reason_id:    str           = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    severity:     FailureSeverity
    category:     str           = Field(description="Short failure category label.")
    description:  str           = Field(description="Human-readable failure description.")
    repair_hint:  str           = Field(default="", description="Suggested repair action.")
    file_path:    Optional[str] = None
    line_number:  Optional[int] = None
    metadata:     Dict[str, Any] = Field(default_factory=dict)


class StructuredFeedback(BaseModel):
    """
    Typed guidance produced by ``FeedbackEngine`` from a ``ValidationReport``.

    This is the primary input to the ``ReasoningEngine`` and ``PlannerAgent``.
    """

    model_config = ConfigDict(frozen=False)

    feedback_id:        str              = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    candidate_id:       str
    iteration:          int
    failure_reasons:    List[FailureReason] = Field(default_factory=list)
    compilation_failed: bool             = False
    regression_failed:  bool             = False
    bug_not_removed:    bool             = False
    new_bugs_introduced: int             = 0
    score_delta:        float            = 0.0    # score − previous_best_score
    primary_suggestion: str             = ""     # Top repair action
    all_suggestions:    List[str]       = Field(default_factory=list)
    timestamp:          float           = Field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Agent Decision
# ---------------------------------------------------------------------------


class AgentDecision(BaseModel):
    """
    The output of any repair agent after executing its role.

    Every agent produces exactly one ``AgentDecision`` per invocation.
    """

    model_config = ConfigDict(frozen=False)

    decision_id:      str           = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    agent_role:       AgentRole
    iteration:        int
    strategy:         Optional[RepairStrategy] = None   # Set by PlannerAgent
    reasoning:        str           = ""
    confidence:       float         = Field(default=0.5, ge=0.0, le=1.0)
    recommended_actions: List[str]  = Field(default_factory=list)
    preserve_elements:   List[str]  = Field(default_factory=list)
    modify_elements:     List[str]  = Field(default_factory=list)
    metadata:         Dict[str, Any] = Field(default_factory=dict)
    duration_ms:      float         = 0.0
    timestamp:        float         = Field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Candidate Entry (pool record)
# ---------------------------------------------------------------------------


class CompositeScore(BaseModel):
    """Detailed breakdown of a candidate's composite repair score."""

    model_config = ConfigDict(frozen=False)

    total:              float = 0.0
    validation_component:    float = 0.0
    confidence_component:    float = 0.0
    risk_component:          float = 0.0
    maintainability_component: float = 0.0
    simplicity_component:    float = 0.0
    static_improvement_component: float = 0.0


class CandidateEntry(BaseModel):
    """
    A single candidate tracked inside ``CandidatePool``.

    Maintains complete provenance: which patch it came from, which
    iteration it was generated in, and its parent candidate (for refinement).
    """

    model_config = ConfigDict(frozen=False)

    entry_id:       str           = Field(default_factory=lambda: str(uuid.uuid4()))
    candidate_id:   str           = ""    # PatchCandidate.candidate_id from 3D.1
    patch_id:       str           = ""    # StructuredPatch.patch_id
    iteration:      int           = 0
    parent_entry_id: Optional[str] = None  # For refinement lineage
    strategy_used:  Optional[RepairStrategy] = None
    refinement_strategy: Optional[RefinementStrategy] = None

    # Scores
    validation_score: float       = 0.0
    composite_score:  Optional[CompositeScore] = None

    # Status
    accepted:       bool          = False
    rejected:       bool          = False
    rejection_reasons: List[str]  = Field(default_factory=list)

    # Snapshot (serialized for pool portability)
    candidate_snapshot: Dict[str, Any] = Field(default_factory=dict)
    feedback_snapshot:  Optional[Dict[str, Any]] = None

    created_at:     float         = Field(default_factory=time.time)
    metadata:       Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Repair Iteration
# ---------------------------------------------------------------------------


class RepairIteration(BaseModel):
    """
    Complete record of one generate→validate→analyze→improve cycle.

    Stored in ``RepairMemory`` and serialized into the ``AuditTrail``.
    """

    model_config = ConfigDict(frozen=False)

    iteration_id:       str           = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    iteration_index:    int
    strategy:           RepairStrategy
    refinement_strategy: Optional[RefinementStrategy] = None

    # Candidates produced this iteration
    candidate_entry_ids: List[str]   = Field(default_factory=list)
    best_candidate_id:  Optional[str] = None

    # Scores
    best_validation_score: float     = 0.0
    best_composite_score:  float     = 0.0

    # Agent decisions (one per role, keyed by AgentRole value)
    agent_decisions:    Dict[str, AgentDecision] = Field(default_factory=dict)

    # Feedback produced this iteration
    feedback:           Optional[StructuredFeedback] = None

    # Outcome
    improved:           bool          = False
    terminated:         bool          = False
    termination_reason: Optional[TerminationReason] = None

    # Timing
    duration_ms:        float         = 0.0
    started_at:         float         = Field(default_factory=time.time)
    completed_at:       Optional[float] = None

    metadata:           Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Repair Report
# ---------------------------------------------------------------------------


class RepairReport(BaseModel):
    """
    Final engineering report produced by ``ReportAgent`` at session end.

    This artifact is returned as part of ``RepairSession`` and can be
    rendered as Markdown, JSON, or embedded in the existing SARIF report.
    """

    model_config = ConfigDict(frozen=False)

    report_id:          str           = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id:         str
    bug_id:             str
    rule_id:            str           = ""

    # Summary
    accepted:           bool          = False
    termination_reason: TerminationReason = TerminationReason.UNKNOWN
    total_iterations:   int           = 0
    best_score:         float         = 0.0
    accepted_candidate_id: Optional[str] = None

    # Narrative
    executive_summary:  str           = ""
    why_candidate_won:  str           = ""
    iteration_summaries: List[str]    = Field(default_factory=list)
    agent_decision_log:  List[str]    = Field(default_factory=list)
    score_progression:  List[float]   = Field(default_factory=list)
    strategies_tried:   List[str]     = Field(default_factory=list)

    # Candidate evolution
    candidate_count:    int           = 0
    accepted_count:     int           = 0
    rejected_count:     int           = 0
    lineage_summary:    str           = ""

    # Timing
    total_duration_ms:  float         = 0.0
    timestamp:          float         = Field(default_factory=time.time)
    metadata:           Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Repair Session (top-level output)
# ---------------------------------------------------------------------------


class RepairSession(BaseModel):
    """
    Top-level output of ``RepairOrchestrator.run()``.

    Contains the complete repair history, the accepted patch, the
    engineering report, and the full audit trail.
    """

    model_config = ConfigDict(frozen=False)

    session_id:         str           = Field(default_factory=lambda: str(uuid.uuid4()))
    bug_id:             str
    rule_id:            str           = ""

    # Outcome
    accepted:           bool          = False
    termination_reason: TerminationReason = TerminationReason.UNKNOWN
    best_candidate_entry_id: Optional[str] = None
    best_composite_score:    float    = 0.0

    # Iteration history (ordered)
    iterations:         List[RepairIteration] = Field(default_factory=list)

    # Engineering report
    report:             Optional[RepairReport] = None

    # Audit trail (JSONL string if export_audit_on_completion=True)
    audit_trail_jsonl:  Optional[str] = None

    # Metrics snapshot
    metrics_snapshot:   Dict[str, Any] = Field(default_factory=dict)

    # Timing
    started_at:         float         = Field(default_factory=time.time)
    completed_at:       Optional[float] = None
    total_duration_ms:  float         = 0.0

    metadata:           Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Audit Event
# ---------------------------------------------------------------------------


class AuditEvent(BaseModel):
    """A single timestamped event in the AuditTrail."""

    model_config = ConfigDict(frozen=False)

    event_id:       str           = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    event_type:     AuditEventType
    session_id:     str           = ""
    iteration:      int           = -1
    agent_role:     Optional[str] = None
    payload:        Dict[str, Any] = Field(default_factory=dict)
    timestamp:      float         = Field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Metrics Snapshot
# ---------------------------------------------------------------------------


class MetricsSnapshot(BaseModel):
    """Point-in-time metrics snapshot for a repair session."""

    model_config = ConfigDict(frozen=False)

    session_id:             str   = ""
    total_iterations:       int   = 0
    total_llm_calls:        int   = 0
    total_agent_invocations: int  = 0
    total_candidates:       int   = 0
    accepted_candidates:    int   = 0
    rejected_candidates:    int   = 0
    best_score_achieved:    float = 0.0
    score_progression:      List[float] = Field(default_factory=list)
    avg_iteration_ms:       float = 0.0
    total_duration_ms:      float = 0.0
    improvement_rate:       float = 0.0   # fraction of iterations that improved score
    strategies_used:        List[str] = Field(default_factory=list)
    timestamp:              float = Field(default_factory=time.time)
