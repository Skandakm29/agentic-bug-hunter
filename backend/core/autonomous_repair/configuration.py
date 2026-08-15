"""
Autonomous Repair Loop — Configuration  (Phase 3D.3)
=====================================================
``RepairConfiguration`` is the single source of truth for all tunable
parameters in the autonomous repair loop.  It is injected into every
component via constructor dependency injection, so no component reads
environment variables or global state directly.

All fields provide production-ready defaults so the system works
out-of-the-box without any explicit configuration.
"""
from __future__ import annotations

from typing import Dict, Optional

from pydantic import BaseModel, ConfigDict, Field


class RepairConfiguration(BaseModel):
    """
    Complete configuration contract for the Autonomous Multi-Agent Repair Loop.

    Parameters are grouped by concern: loop control, acceptance policy,
    agent behaviour, LLM settings, memory/audit limits, and logging.
    """

    model_config = ConfigDict(frozen=False)

    # ------------------------------------------------------------------
    # Loop control
    # ------------------------------------------------------------------
    max_iterations: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Hard cap on repair iterations before terminating.",
    )
    timeout_seconds: float = Field(
        default=300.0,
        gt=0.0,
        description="Wall-clock timeout (seconds) for the entire repair session.",
    )
    convergence_window: int = Field(
        default=2,
        ge=1,
        description="Number of consecutive iterations with negligible score improvement "
        "before declaring convergence.",
    )
    convergence_delta: float = Field(
        default=0.01,
        ge=0.0,
        le=0.5,
        description="Minimum score improvement per iteration to avoid convergence trigger.",
    )
    max_consecutive_failures: int = Field(
        default=3,
        ge=1,
        description="Maximum consecutive total-failure iterations before aborting.",
    )

    # ------------------------------------------------------------------
    # Acceptance policy
    # ------------------------------------------------------------------
    acceptance_threshold: float = Field(
        default=0.75,
        ge=0.0,
        le=1.0,
        description="Minimum composite repair score required to accept a candidate.",
    )
    termination_policy: str = Field(
        default="any",
        description=(
            "When to stop: 'success' (only on acceptance), "
            "'iteration_limit' (only on max_iterations), "
            "'any' (whichever condition fires first)."
        ),
    )

    # ------------------------------------------------------------------
    # Agent behaviour
    # ------------------------------------------------------------------
    agents_enabled: Dict[str, bool] = Field(
        default_factory=lambda: {
            "validator": True,
            "patch_generator": True,
            "reviewer": True,
            "planner": True,
            "report": True,
        },
        description="Per-agent enable/disable flags.  Disabled agents are skipped.",
    )
    retry_on_agent_failure: bool = Field(
        default=True,
        description="If True, a single agent execution failure triggers a retry "
        "rather than aborting the iteration.",
    )
    agent_timeout_seconds: float = Field(
        default=60.0,
        gt=0.0,
        description="Per-agent wall-clock timeout before raising AgentTimeoutError.",
    )

    # ------------------------------------------------------------------
    # LLM settings
    # ------------------------------------------------------------------
    model_name: Optional[str] = Field(
        default=None,
        description="Override LLM model name for all agents.  None = use provider default.",
    )
    temperature: float = Field(
        default=0.2,
        ge=0.0,
        le=2.0,
        description="Default sampling temperature for agent LLM calls.",
    )
    max_tokens: int = Field(
        default=4096,
        ge=128,
        description="Default max output tokens for agent LLM calls.",
    )
    reasoning_temperature: float = Field(
        default=0.1,
        ge=0.0,
        le=2.0,
        description="Temperature for the ReasoningEngine — lower = more deterministic.",
    )
    refinement_temperature: float = Field(
        default=0.25,
        ge=0.0,
        le=2.0,
        description="Temperature for the RefinementEngine — slightly higher for creativity.",
    )

    # ------------------------------------------------------------------
    # Candidate generation
    # ------------------------------------------------------------------
    candidates_per_iteration: int = Field(
        default=2,
        ge=1,
        le=5,
        description="Number of new patch candidates to generate per iteration.",
    )
    prefer_refinement: bool = Field(
        default=True,
        description="If True, prefer refining existing patches over generating new ones "
        "when a close-but-failing candidate exists.",
    )

    # ------------------------------------------------------------------
    # Memory / audit
    # ------------------------------------------------------------------
    max_memory_entries: int = Field(
        default=50,
        ge=5,
        description="Maximum number of iteration records kept in RepairMemory "
        "before oldest entries are evicted.",
    )
    enable_audit_trail: bool = Field(
        default=True,
        description="If True, AuditTrail records all events for session reproducibility.",
    )
    export_audit_on_completion: bool = Field(
        default=True,
        description="If True, JSONL audit export is attached to the RepairSession.",
    )

    # ------------------------------------------------------------------
    # Scoring weights (must sum to ≤ 1.0; remainder is unweighted)
    # ------------------------------------------------------------------
    weight_validation: float = Field(
        default=0.40,
        ge=0.0,
        le=1.0,
        description="Weight of validation_score in composite repair score.",
    )
    weight_confidence: float = Field(
        default=0.20,
        ge=0.0,
        le=1.0,
        description="Weight of LLM-reported candidate confidence.",
    )
    weight_risk: float = Field(
        default=0.15,
        ge=0.0,
        le=1.0,
        description="Weight of (1 − estimated_risk) in composite score.",
    )
    weight_maintainability: float = Field(
        default=0.10,
        ge=0.0,
        le=1.0,
        description="Weight of ReviewerAgent maintainability score.",
    )
    weight_simplicity: float = Field(
        default=0.10,
        ge=0.0,
        le=1.0,
        description="Weight of patch simplicity (inverse lines changed).",
    )
    weight_static_improvement: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        description="Weight of static-analysis improvement (no new bugs bonus).",
    )
