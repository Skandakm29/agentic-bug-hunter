import json
from typing import Dict
from backend.core.patch_validation.validation_models import ValidationReport, CandidateRanking, ValidationMetrics

class ValidationReportGenerator:
    """Formats validation reports into JSON, Markdown, and SARIF schemas."""

    @staticmethod
    def to_json(report: ValidationReport) -> str:
        """Serialize validation report to JSON string."""
        return json.dumps(report.model_dump(), indent=2)

    @staticmethod
    def to_markdown(report: ValidationReport) -> str:
        """Serialize validation report to a rich Markdown document."""
        md = []
        md.append(f"# Argus Patch Validation Report")
        md.append(f"**Bug ID:** `{report.bug_id}` | **Patch ID:** `{report.patch_id}`")
        md.append(f"**Accepted:** {'🟢 YES' if report.accepted else '🔴 NO'}")
        md.append(f"**Winner Candidate:** `{report.winner_candidate_id or 'None'}`")
        md.append("")
        
        # 1. Rankings Table
        md.append("## Candidate Rankings")
        md.append("| Rank | Candidate ID | Score | Winner | Key Reasons |")
        md.append("|------|--------------|-------|--------|-------------|")
        for rank in report.rankings:
            winner_str = "⭐ YES" if rank.winner else "NO"
            reasons_str = "; ".join(rank.reasons[:2])
            md.append(f"| {rank.rank} | `{rank.candidate_id}` | {rank.score:.3f} | {winner_str} | {reasons_str} |")
        md.append("")

        # 2. Performance Metrics
        md.append("## Metrics Breakdown")
        for cid, m in report.metrics.items():
            md.append(f"### Candidate `{cid}`")
            md.append(f"- **Compilation:** {'Success' if m.compilation_success else 'Failed'}")
            md.append(f"- **Syntax Check:** {'Passed' if m.syntax_success else 'Failed'}")
            md.append(f"- **Bug Removed:** {'Yes' if m.bug_removal_rate == 1.0 else 'No'}")
            md.append(f"- **Regression Status:** {'Passed' if m.regression_success else 'Failed'}")
            md.append(f"- **New Bugs Introduced:** {m.new_bug_count}")
            md.append(f"- **Warning Delta:** {m.warning_delta:+d}")
            md.append(f"- **Lines Touched:** {m.lines_changed}")
            md.append(f"- **Validation Time:** {m.duration_ms:.1f}ms")
            md.append(f"- **Overall Score:** `{m.score:.3f}`")
            md.append("")

        # 3. Diagnostics section
        md.append("## Diagnostics")
        diag = report.diagnostics
        if diag.errors:
            md.append("### Errors")
            for err in diag.errors:
                md.append(f"- ❌ {err}")
            md.append("")
        if diag.warnings:
            md.append("### Warnings")
            for wrn in diag.warnings:
                md.append(f"- ⚠️ {wrn}")
            md.append("")
            
        md.append("### Timing Logs")
        for step, duration in diag.timing.items():
            md.append(f"- **{step}:** {duration:.1f}ms")
            
        return "\n".join(md)

    @staticmethod
    def to_sarif(report: ValidationReport) -> str:
        """Serialize validation diagnostics and outcomes into a compliant SARIF file structure."""
        results = []
        
        # Report validation errors as SARIF results
        for idx, err in enumerate(report.diagnostics.errors):
            results.append({
                "ruleId": "ARGUS_VAL_ERR",
                "message": {"text": err},
                "level": "error",
                "locations": [{
                    "physicalLocation": {
                        "artifactLocation": {
                            "uri": report.diagnostics.affected_files[0] if report.diagnostics.affected_files else "unknown"
                        }
                    }
                }]
            })

        for idx, wrn in enumerate(report.diagnostics.warnings):
            results.append({
                "ruleId": "ARGUS_VAL_WARN",
                "message": {"text": wrn},
                "level": "warning",
                "locations": [{
                    "physicalLocation": {
                        "artifactLocation": {
                            "uri": report.diagnostics.affected_files[0] if report.diagnostics.affected_files else "unknown"
                        }
                    }
                }]
            })

        sarif = {
            "$schema": "https://schemastore.azurewebsites.net/schemas/sarif/sarif-2.1.0-rtm.5.json",
            "version": "2.1.0",
            "runs": [
                {
                    "tool": {
                        "driver": {
                            "name": "Argus Patch Validator",
                            "version": "3D.2",
                            "informationUri": "https://github.com/Harsha2oo5/Argus"
                        }
                    },
                    "results": results
                }
            ]
        }
        return json.dumps(sarif, indent=2)
