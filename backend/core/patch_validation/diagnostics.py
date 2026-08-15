from typing import Dict, List
from backend.core.patch_validation.validation_models import Diagnostics


class DiagnosticsCollector:
    """Collector helper facilitating step-by-step assembly of Diagnostics details."""

    def __init__(self) -> None:
        self._errors: List[str] = []
        self._warnings: List[str] = []
        self._timing: Dict[str, float] = {}
        self._affected_files: List[str] = []
        self._suggested_actions: List[str] = []

    def add_error(self, error: str) -> None:
        """Add error message."""
        if error and error not in self._errors:
            self._errors.append(error)

    def add_warning(self, warning: str) -> None:
        """Add warning message."""
        if warning and warning not in self._warnings:
            self._warnings.append(warning)

    def record_timing(self, step: str, time_ms: float) -> None:
        """Record execution duration of a validation step."""
        self._timing[step] = round(time_ms, 2)

    def add_affected_file(self, file_path: str) -> None:
        """Track file touched by patch validation."""
        if file_path and file_path not in self._affected_files:
            self._affected_files.append(file_path)

    def add_suggested_action(self, action: str) -> None:
        """Append suggested action item for failure recovery."""
        if action and action not in self._suggested_actions:
            self._suggested_actions.append(action)

    def to_diagnostics(self) -> Diagnostics:
        """Construct Pydantic Diagnostics model."""
        return Diagnostics(
            errors=list(self._errors),
            warnings=list(self._warnings),
            timing=dict(self._timing),
            affected_files=list(self._affected_files),
            suggested_actions=list(self._suggested_actions)
        )
