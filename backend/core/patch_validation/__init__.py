"""
Autonomous Patch Validation Engine (Phase 3D.2)
==============================================
Validates AI-generated patch candidates inside isolated workspaces using syntax validation,
compilation, static reanalysis, regression test suites execution, metrics compilation,
rollback, and candidate ranking.
"""

from backend.core.patch_validation.validation_engine import ValidationEngine
from backend.core.patch_validation.configuration import PatchValidationConfig
from backend.core.patch_validation.validation_models import (
    Workspace,
    CompilationResult,
    RegressionResult,
    StaticReanalysisResult,
    ValidationMetrics,
    Diagnostics,
    CandidateRanking,
    ValidationReport,
)
from backend.core.patch_validation.validation_report import ValidationReportGenerator
from backend.core.patch_validation.workspace_manager import WorkspaceManager
from backend.core.patch_validation.exceptions import (
    PatchValidationError,
    WorkspaceError,
    PatchApplyError,
    SyntaxError,
    CompilationError,
    BuildSystemError,
    TestError,
    StaticAnalysisError,
    TimeoutError,
)

__all__ = [
    # Engine
    "ValidationEngine",
    "PatchValidationConfig",
    
    # Models
    "Workspace",
    "CompilationResult",
    "RegressionResult",
    "StaticReanalysisResult",
    "ValidationMetrics",
    "Diagnostics",
    "CandidateRanking",
    "ValidationReport",
    
    # Report formatters
    "ValidationReportGenerator",
    
    # Workspace management
    "WorkspaceManager",
    
    # Exceptions
    "PatchValidationError",
    "WorkspaceError",
    "PatchApplyError",
    "SyntaxError",
    "CompilationError",
    "BuildSystemError",
    "TestError",
    "StaticAnalysisError",
    "TimeoutError",
]
