from pydantic import BaseModel, Field


class PatchValidationConfig(BaseModel):
    """Configuration class for the Autonomous Patch Validation Engine."""

    compiler_type: str = Field(default="gcc", description="Compiler to use (gcc, clang, msvc).")
    build_system: str = Field(default="cmake", description="Build system to use (cmake, make, meson, ninja, bazel, none).")
    timeout_seconds: int = Field(default=60, description="Overall timeout limit for validation steps.")
    parallel_jobs: int = Field(default=4, description="Number of parallel compiler/test runner jobs.")
    workspace_type: str = Field(default="temp_dir", description="Isolation model: temp_dir, git_worktree, none.")
    warning_threshold: int = Field(default=10, description="Maximum compiler warnings allowed before scoring deduction.")
    min_acceptance_score: float = Field(default=0.7, description="Minimum overall score required to recommend a candidate.")
    regression_enabled: bool = Field(default=True, description="Enable running regression test suites.")
    static_analysis_enabled: bool = Field(default=True, description="Enable re-running the static analysis engine.")
    build_directory: str = Field(default="build", description="Name of the subdirectory where build outputs reside.")
