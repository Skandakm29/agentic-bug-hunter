from typing import Any, List, Optional
from pydantic import BaseModel, Field


class NormalizedFinding(BaseModel):
    """Unified schema representing a validated issue flagged by the static analysis engines."""
    rule_id: str = Field(description="The unique identifier/tag of the static analysis rule.")
    file_path: Optional[str] = Field(default=None, description="Optional path to the scanned file.")
    line_number: Optional[int] = Field(default=None, description="Optional 1-indexed line number of the code anomaly.")
    line_text: str = Field(description="The exact text snippet flagged by the check.")
    severity: str = Field(description="Categorized priority severity: CRITICAL, HIGH, MEDIUM, LOW.")
    static_confidence: float = Field(description="Rule deterministic confidence rating between 0.0 and 1.0.")
    description: str = Field(description="Clear explanation detailing the rule violation.")
    evidence: str = Field(description="Code fragments or tokens that triggered the rule.")
    remediation: str = Field(description="Actionable guide describing how to resolve the finding.")
    source: str = Field(default="static", description="Finding detection origin.")


class AnalysisReport(BaseModel):
    """Structured report returned back to presentation API routers and clients."""
    static_findings: List[NormalizedFinding]
    llm_result: Optional[Any] = Field(default=None, description="Validated LLM analysis findings and code fixes.")
    total_issues: int = Field(description="Total count of warnings flagged by static engines.")
    llm_available: bool = Field(description="True if validation agent was reachable and completed checks.")
