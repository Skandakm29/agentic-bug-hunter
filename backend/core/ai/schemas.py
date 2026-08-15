from typing import Any, List, Optional
from pydantic import BaseModel, Field


class BugValidationResult(BaseModel):
    """Schema for validating whether a static engine finding is a real bug."""
    valid_bug: bool = Field(description="True if the warning is a genuine bug, False if it is a false positive.")
    explanation: str = Field(description="Detailed explanation of the issue, risk, and context.")
    confidence: float = Field(description="Certainty score between 0.0 and 1.0.")


class CodeCorrection(BaseModel):
    """Schema for generating C++ code corrections."""
    corrected_code: str = Field(description="The complete refactored/fixed line or block of code.")


class FindingDetail(BaseModel):
    """Schema representing an individual static finding details."""
    line_number: Optional[int] = None
    line_text: str
    rule_tag: str
    description: str
    confidence: float
    source: str = "static"


class AnalysisReport(BaseModel):
    """Schema representing the final report returned to the user."""
    static_findings: List[FindingDetail]
    llm_result: Optional[Any] = None
    total_issues: int
    llm_available: bool
