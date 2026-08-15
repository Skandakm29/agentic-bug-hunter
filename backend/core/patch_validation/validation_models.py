import time
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field


class Workspace(BaseModel):
    """Represents an isolated build and verification directory."""
    path: str
    original_path: str
    workspace_type: str
    created_at: float = Field(default_factory=time.time)


class CompilationResult(BaseModel):
    """Result of running compilation on the patched workspace."""
    success: bool
    compiler: str
    stdout: str = ""
    stderr: str = ""
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    compile_time_ms: float = 0.0


class RegressionResult(BaseModel):
    """Result of running regression test suite on the patched codebase."""
    success: bool
    pass_count: int = 0
    fail_count: int = 0
    skipped: int = 0
    execution_time_ms: float = 0.0
    details: str = ""


class StaticReanalysisResult(BaseModel):
    """Result of running post-patch static validation reanalysis."""
    success: bool
    original_bug_removed: bool
    new_findings_count: int = 0
    warning_delta: int = 0
    before_findings_count: int = 0
    after_findings_count: int = 0
    details: str = ""


class ValidationMetrics(BaseModel):
    """Execution metrics and final score for a specific patch candidate."""
    compilation_success: bool
    syntax_success: bool
    bug_removal_rate: float  # 0.0 to 1.0
    regression_success: bool
    new_bug_count: int = 0
    warning_delta: int = 0
    lines_changed: int = 0
    patch_size: int = 0  # character count of diff
    duration_ms: float = 0.0
    score: float = 0.0


class Diagnostics(BaseModel):
    """Structured warnings, timing information, and troubleshooting steps."""
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    timing: Dict[str, float] = Field(default_factory=dict)
    affected_files: List[str] = Field(default_factory=list)
    suggested_actions: List[str] = Field(default_factory=list)


class CandidateRanking(BaseModel):
    """Details how a candidate performed relative to other candidates."""
    candidate_id: str
    score: float
    rank: int
    winner: bool
    reasons: List[str] = Field(default_factory=list)


class ValidationReport(BaseModel):
    """Top-level unified report capturing validation outcomes for all candidates."""
    patch_id: str
    bug_id: str
    winner_candidate_id: Optional[str] = None
    rankings: List[CandidateRanking] = Field(default_factory=list)
    diagnostics: Diagnostics = Field(default_factory=lambda: Diagnostics())
    metrics: Dict[str, ValidationMetrics] = Field(default_factory=dict)
    accepted: bool = False
    timestamp: float = Field(default_factory=time.time)
