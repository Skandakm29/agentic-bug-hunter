class PatchValidationError(Exception):
    """Base class for all errors raised during patch validation."""
    def __init__(self, message: str, details: dict = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class WorkspaceError(PatchValidationError):
    """Raised when workspace creation, isolation, or setup fails."""
    pass


class PatchApplyError(PatchValidationError):
    """Raised when applying a patch diff fails or encounters merge conflicts."""
    pass


class SyntaxError(PatchValidationError):
    """Raised when the patched code violates language syntax rules."""
    pass


class CompilationError(PatchValidationError):
    """Raised when the compilation of the patched codebase fails."""
    pass


class BuildSystemError(PatchValidationError):
    """Raised when the build configuration or setup fails."""
    pass


class TestError(PatchValidationError):
    """Raised during regression test discovery or test runner execution."""
    pass


class StaticAnalysisError(PatchValidationError):
    """Raised when static analysis engine fails during post-patch reanalysis."""
    pass


class TimeoutError(PatchValidationError):
    """Raised when a validation step exceeds its configured time limit."""
    pass
