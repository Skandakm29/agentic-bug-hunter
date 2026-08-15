from abc import ABC, abstractmethod
from typing import Any, Dict, List
from pydantic import BaseModel


class CodeLine(BaseModel):
    """Normalized representation of a single code line."""
    line_number: int
    text: str
    stripped: str
    is_comment: bool
    in_isr: bool
    brace_depth: int
    contains_rdi: bool


class CodeRepresentation(BaseModel):
    """File-level Intermediate Representation (IR) containing parsed syntax metadata."""
    lines: List[CodeLine]
    raw_code: str
    metadata: Dict[str, Any]


class BaseParser(ABC):
    """Abstract interface for all programming language code parsers."""

    @abstractmethod
    def parse(self, code: str) -> CodeRepresentation:
        """Parse source code into a normalized CodeRepresentation IR."""
        pass
