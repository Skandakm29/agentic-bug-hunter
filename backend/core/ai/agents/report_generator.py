from typing import List, Optional
from backend.core.ai.schemas import BugValidationResult, CodeCorrection, AnalysisReport, FindingDetail
from backend.core.ai.agents.base import BaseAgent


class ReportGeneratorAgent(BaseAgent):
    """Agent responsible for compiling analysis findings and LLM results into a unified report."""

    def compile_report(
        self,
        findings: List[dict],
        validation_result: Optional[BugValidationResult],
        correction: Optional[CodeCorrection],
        combined_confidence: float
    ) -> dict:
        # NormalizedFinding serialises the identifier as `rule_id`; the client
        # contract (FindingDetail / the UI) calls the same value `rule_tag`.
        # Reading only `rule_tag` here made every finding render as "unknown".
        static_details = [
            FindingDetail(
                line_number=f.get("line_number"),
                line_text=f.get("line_text", ""),
                rule_tag=f.get("rule_id") or f.get("rule_tag") or "unknown",
                description=f.get("description", ""),
                confidence=f.get("static_confidence", 0.5),
                source="static"
            )
            for f in findings
        ]

        llm_result_dict = None
        if validation_result:
            llm_result_dict = {
                "valid_bug": validation_result.valid_bug,
                "explanation": validation_result.explanation,
                "corrected_code": correction.corrected_code if correction else "",
                "confidence": combined_confidence
            }

        report = AnalysisReport(
            static_findings=static_details,
            llm_result=llm_result_dict,
            total_issues=len(findings),
            llm_available=llm_result_dict is not None
        )

        return report.model_dump()
