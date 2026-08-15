import os
import logging
from collections import Counter
from typing import List, Tuple
from backend.core.analysis.engine import AnalysisEngine
from backend.core.analysis.schemas import NormalizedFinding
from backend.core.patch_validation.validation_models import StaticReanalysisResult
from backend.core.patch_validation.exceptions import StaticAnalysisError

logger = logging.getLogger("backend.patch_validation.static_validator")


class StaticValidator:
    """Re-runs the static analysis pipeline to compare findings before and after patch application."""

    def __init__(self) -> None:
        self.engine = AnalysisEngine()

    @staticmethod
    def _signature(finding: NormalizedFinding) -> Tuple[str, str]:
        """
        Identity of a finding that is stable across the line renumbering a
        patch causes, but still distinguishes separate occurrences of the
        same rule within one file.
        """
        return (finding.rule_id, (finding.line_text or "").strip())

    def validate_patch(
        self,
        original_file_path: str,
        patched_file_path: str,
        original_finding: dict
    ) -> StaticReanalysisResult:
        """
        Compare static findings between original and patched versions.

        Parameters
        ----------
        original_file_path : Absolute path to the original source file.
        patched_file_path : Absolute path to the patched source file.
        original_finding : Dict representing the target finding being resolved.
        """
        if not os.path.exists(original_file_path):
            raise StaticAnalysisError(f"Original file not found: {original_file_path}")
        if not os.path.exists(patched_file_path):
            raise StaticAnalysisError(f"Patched file not found: {patched_file_path}")

        try:
            with open(original_file_path, "r", encoding="utf-8", errors="ignore") as f:
                original_code = f.read()
            with open(patched_file_path, "r", encoding="utf-8", errors="ignore") as f:
                patched_code = f.read()

            # Run analysis before and after patch application
            ext = os.path.splitext(original_file_path)[1].replace(".", "") or "cpp"
            
            before_findings = self.engine.analyze_plain(original_code, extension=ext)
            after_findings = self.engine.analyze_plain(patched_code, extension=ext)

            # ── Compare findings by signature, not line proximity ──────
            #
            # Matching on "same rule_id within +/-5 lines" conflates two
            # different things: the bug we tried to fix, and an unrelated
            # occurrence of the same rule that happens to sit nearby. A file
            # with two hits for one rule a few lines apart would report the
            # bug as unfixed even after the patch removed it.
            #
            # A signature of (rule_id, flagged source text) survives the line
            # shifts a patch introduces while still distinguishing separate
            # occurrences.
            target_rule_id = original_finding.get("rule_id")
            target_line    = original_finding.get("line_number")
            target_text    = (original_finding.get("line_text") or "").strip()

            before_sigs = Counter(self._signature(f) for f in before_findings)
            after_sigs  = Counter(self._signature(f) for f in after_findings)

            before_count = sum(1 for f in before_findings if f.rule_id == target_rule_id)
            after_count  = sum(1 for f in after_findings  if f.rule_id == target_rule_id)
            count_decreased = before_count > after_count

            if target_text:
                # Two independent signals, both required.
                #
                # Signature alone is not enough: appending a comment to the
                # offending line changes its text without fixing anything, and
                # the old signature would vanish.
                #
                # Count alone is not enough either: it cannot tell which of
                # several same-rule hits went away.
                #
                # Demanding both keeps the validator conservative -- it errs
                # toward "not fixed", which is the safe direction for a gate
                # that decides whether to apply a patch automatically.
                signature_gone = after_sigs[(target_rule_id, target_text)] == 0
                original_bug_removed = signature_gone and count_decreased
            elif before_count:
                # No flagged text available; occurrence counting is all we have.
                original_bug_removed = count_decreased
            else:
                # The rule never fired on the original file, so there is
                # nothing to have removed. Do not claim success.
                original_bug_removed = False

            # Identify findings the patch introduced: signatures present after
            # but not before (multiset difference excludes pre-existing ones).
            introduced = after_sigs - before_sigs
            new_findings: List[NormalizedFinding] = [
                f for f in after_findings
                if introduced.get(self._signature(f), 0) > 0
                and f.rule_id != target_rule_id
            ]

            warning_delta = len(after_findings) - len(before_findings)

            details_msg = (
                f"Static Reanalysis: original_bug_removed={original_bug_removed}, "
                f"before_count={len(before_findings)}, after_count={len(after_findings)}, "
                f"new_introduced={len(new_findings)}"
            )
            logger.info(details_msg)

            return StaticReanalysisResult(
                success=True,
                original_bug_removed=original_bug_removed,
                new_findings_count=len(new_findings),
                warning_delta=warning_delta,
                before_findings_count=len(before_findings),
                after_findings_count=len(after_findings),
                details=details_msg
            )

        except Exception as e:
            logger.error(f"Static validation reanalysis failed: {e}")
            raise StaticAnalysisError(f"Static analysis failed during verification: {e}") from e
