"""
Autonomous Repair Loop — Feedback Engine  (Phase 3D.3)
=======================================================
``FeedbackEngine`` converts raw ``ValidationReport`` + ``ValidationMetrics``
into ``StructuredFeedback`` — a ranked list of typed failure reasons with
concrete repair hints.

Design invariants
-----------------
- Pure function: no LLM calls, no I/O, no side effects.
- Deterministic: same inputs always produce the same output.
- Extensible: new failure pattern matchers can be registered via
  ``register_pattern()``.
- Fast: designed to run in < 1 ms for any practical validation report.
"""
from __future__ import annotations

import logging
import re
from typing import Callable, Dict, List, Optional, Tuple

from backend.core.autonomous_repair.repair_models import (
    FailureReason,
    FailureSeverity,
    StructuredFeedback,
)
from backend.core.patch_validation.validation_models import (
    ValidationMetrics,
    ValidationReport,
)

logger = logging.getLogger("backend.autonomous_repair.feedback_engine")

# Type alias for a pattern matcher function
PatternMatcher = Callable[
    [ValidationReport, ValidationMetrics, str],
    Optional[FailureReason],
]


class FeedbackEngine:
    """
    Converts raw validation output into ranked, actionable ``StructuredFeedback``.

    Built-in pattern matchers handle the most common failure classes.
    Custom matchers can be added at runtime via ``register_pattern()``.
    """

    def __init__(self) -> None:
        self._patterns: List[PatternMatcher] = _build_default_patterns()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register_pattern(self, matcher: PatternMatcher) -> None:
        """Add a custom failure pattern matcher to the front of the priority list."""
        self._patterns.insert(0, matcher)

    def extract(
        self,
        report: ValidationReport,
        metrics: ValidationMetrics,
        candidate_id: str,
        iteration: int,
        previous_best_score: float = 0.0,
    ) -> StructuredFeedback:
        """
        Extract structured feedback from a validation report.

        Parameters
        ----------
        report : ``ValidationReport`` produced by the 3D.2 ValidationEngine.
        metrics : ``ValidationMetrics`` for the specific candidate.
        candidate_id : The candidate being analysed.
        iteration : Current repair iteration index.
        previous_best_score : Best score seen in earlier iterations.

        Returns
        -------
        ``StructuredFeedback`` with ranked failure reasons and repair hints.
        """
        failure_reasons: List[FailureReason] = []

        for matcher in self._patterns:
            reason = matcher(report, metrics, candidate_id)
            if reason is not None:
                failure_reasons.append(reason)

        # Sort by severity (CRITICAL first)
        _severity_order = {
            FailureSeverity.CRITICAL: 0,
            FailureSeverity.HIGH: 1,
            FailureSeverity.MEDIUM: 2,
            FailureSeverity.LOW: 3,
        }
        failure_reasons.sort(key=lambda r: _severity_order.get(r.severity, 99))

        # Build suggestion list
        all_suggestions = [r.repair_hint for r in failure_reasons if r.repair_hint]
        primary = all_suggestions[0] if all_suggestions else ""

        score_delta = metrics.score - previous_best_score

        feedback = StructuredFeedback(
            candidate_id=candidate_id,
            iteration=iteration,
            failure_reasons=failure_reasons,
            compilation_failed=not metrics.compilation_success,
            regression_failed=not metrics.regression_success,
            bug_not_removed=(metrics.bug_removal_rate < 0.5),
            new_bugs_introduced=metrics.new_bug_count,
            score_delta=round(score_delta, 4),
            primary_suggestion=primary,
            all_suggestions=all_suggestions,
        )

        logger.debug(
            "FeedbackEngine: candidate=%s, iteration=%d, reasons=%d, primary=%r",
            candidate_id,
            iteration,
            len(failure_reasons),
            primary,
        )
        return feedback


# ---------------------------------------------------------------------------
# Built-in pattern matchers
# ---------------------------------------------------------------------------


def _pattern_compilation_failure(
    report: ValidationReport,
    metrics: ValidationMetrics,
    candidate_id: str,
) -> Optional[FailureReason]:
    if metrics.compilation_success:
        return None
    # Examine diagnostics for actionable clues
    hint = "Fix compilation errors before any other repair steps."
    errors_text = " ".join(report.diagnostics.errors).lower()
    if "missing" in errors_text and "include" in errors_text:
        hint = "Add the required #include directive for the missing symbol."
    elif "undefined" in errors_text:
        hint = "Resolve undefined symbol: check function signature or missing declaration."
    elif "syntax" in errors_text or "expected" in errors_text:
        hint = "Correct the syntax error introduced by the patch."
    elif "redefinition" in errors_text:
        hint = "Remove duplicate definition introduced by the patch."
    return FailureReason(
        severity=FailureSeverity.CRITICAL,
        category="compilation_failure",
        description="The patched code failed to compile.",
        repair_hint=hint,
    )


def _pattern_syntax_failure(
    report: ValidationReport,
    metrics: ValidationMetrics,
    candidate_id: str,
) -> Optional[FailureReason]:
    if metrics.syntax_success:
        return None
    return FailureReason(
        severity=FailureSeverity.CRITICAL,
        category="syntax_failure",
        description="The patched code failed the structural syntax check "
                    "(unbalanced braces/parens or malformed preprocessor).",
        repair_hint="Ensure all braces, parentheses, and brackets are balanced. "
                    "Validate preprocessor directive pairing.",
    )


def _pattern_bug_not_removed(
    report: ValidationReport,
    metrics: ValidationMetrics,
    candidate_id: str,
) -> Optional[FailureReason]:
    if metrics.bug_removal_rate >= 0.5:
        return None
    return FailureReason(
        severity=FailureSeverity.HIGH,
        category="bug_not_removed",
        description="Static reanalysis still detects the original bug after applying the patch.",
        repair_hint="The patch did not address the root cause. Target the exact "
                    "line range identified in the finding and ensure the "
                    "problematic code path is eliminated.",
    )


def _pattern_regression_failure(
    report: ValidationReport,
    metrics: ValidationMetrics,
    candidate_id: str,
) -> Optional[FailureReason]:
    if metrics.regression_success:
        return None
    hint = "The patch broke existing tests. Review what behaviour changed and " \
           "ensure the fix is narrowly scoped to the bug site."
    # Check diagnostics for specific test failure details
    diag_text = " ".join(report.diagnostics.errors + report.diagnostics.warnings).lower()
    if "assertion" in diag_text:
        hint = "An assertion failed — the patch likely changed expected return " \
               "values or side effects. Narrow the change scope."
    elif "segfault" in diag_text or "segmentation" in diag_text:
        hint = "Segmentation fault in tests — the patch may introduce a null " \
               "dereference or out-of-bounds access."
    return FailureReason(
        severity=FailureSeverity.HIGH,
        category="regression_failure",
        description="One or more regression tests failed after patch application.",
        repair_hint=hint,
    )


def _pattern_new_bugs_introduced(
    report: ValidationReport,
    metrics: ValidationMetrics,
    candidate_id: str,
) -> Optional[FailureReason]:
    if metrics.new_bug_count == 0:
        return None
    return FailureReason(
        severity=FailureSeverity.HIGH,
        category="new_bugs_introduced",
        description=f"The patch introduced {metrics.new_bug_count} new static finding(s).",
        repair_hint="Simplify the patch to avoid introducing new issues. "
                    "Check for new null dereferences, resource leaks, or "
                    "API misuse patterns in the added code.",
    )


def _pattern_low_score(
    report: ValidationReport,
    metrics: ValidationMetrics,
    candidate_id: str,
) -> Optional[FailureReason]:
    if metrics.score >= 0.5:
        return None
    return FailureReason(
        severity=FailureSeverity.MEDIUM,
        category="low_score",
        description=f"Candidate validation score ({metrics.score:.3f}) is below the midpoint.",
        repair_hint="Address compilation and regression failures first, then "
                    "ensure the original bug is fully removed.",
    )


def _pattern_warning_spike(
    report: ValidationReport,
    metrics: ValidationMetrics,
    candidate_id: str,
) -> Optional[FailureReason]:
    if metrics.warning_delta <= 3:
        return None
    return FailureReason(
        severity=FailureSeverity.MEDIUM,
        category="warning_increase",
        description=f"The patch increased compiler warnings by {metrics.warning_delta}.",
        repair_hint="Review and eliminate new compiler warnings. Unsafe type "
                    "casts, signed/unsigned mismatches, or unused variables "
                    "are common causes.",
    )


def _pattern_excessive_size(
    report: ValidationReport,
    metrics: ValidationMetrics,
    candidate_id: str,
) -> Optional[FailureReason]:
    if metrics.lines_changed <= 30:
        return None
    return FailureReason(
        severity=FailureSeverity.LOW,
        category="patch_too_large",
        description=f"The patch modifies {metrics.lines_changed} lines — consider a more targeted fix.",
        repair_hint="Reduce the patch scope. Focus only on the function or "
                    "block containing the bug; avoid reformatting unrelated code.",
    )


def _build_default_patterns() -> List[PatternMatcher]:
    """Return the ordered list of default pattern matchers."""
    return [
        _pattern_compilation_failure,
        _pattern_syntax_failure,
        _pattern_bug_not_removed,
        _pattern_regression_failure,
        _pattern_new_bugs_introduced,
        _pattern_low_score,
        _pattern_warning_spike,
        _pattern_excessive_size,
    ]
