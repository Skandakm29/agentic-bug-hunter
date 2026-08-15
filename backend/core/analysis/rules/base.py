from abc import ABC, abstractmethod
from typing import List
from backend.core.analysis.parsers.base import CodeRepresentation
from backend.core.analysis.schemas import NormalizedFinding


class BaseRule(ABC):
    """Abstract base class for all static analysis rules, defining metadata fields and lifecycle."""

    rule_id: str
    name: str
    description: str
    category: str
    language: str
    severity: str  # "CRITICAL", "HIGH", "MEDIUM", "LOW"
    confidence: float

    @abstractmethod
    def execute(self, representation: CodeRepresentation) -> List[NormalizedFinding]:
        """
        Execute rule checks against a CodeRepresentation intermediate representation (IR).
        Returns a list of NormalizedFinding matches.
        """
        pass
