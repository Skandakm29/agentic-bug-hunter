"""
False Positive Reduction Framework
====================================
Enterprise-grade suppression pipeline that filters NormalizedFindings before
they surface to developers or downstream consumers.

Architecture
------------
Suppression is applied as a sequential pipeline of independent Filter nodes.
Each filter receives the full finding list and returns a (passed, suppressed)
split.  The pipeline aggregates results; any finding suppressed by at least
one filter is withheld from the output list but ALWAYS retained in the audit
log for full traceability.

Built-in filters (ordered by cost, cheapest first)
───────────────────────────────────────────────────
1. ConfidenceThresholdFilter   – drops findings below min_confidence
2. ManualSuppressFilter        – drops rule_ids explicitly listed in config
3. DuplicateFilter             – drops findings identical on (rule_id, file, line)
4. CommentLineFilter           – drops findings whose flagged line is a comment
5. ArchitectureLayerFilter     – drops findings in explicitly whitelisted layers

Extensibility
-------------
Custom filters MUST inherit from ``BaseSuppressionFilter`` and be registered
with ``SuppressionPipeline.register_filter()``.

Auditability
------------
Every suppressed finding is wrapped in a ``SuppressedFindingRecord`` that
records the suppressor name and reason.  Records are accessible via
``SuppressionPipeline.audit_log`` after a run.
"""
import logging
import re
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Set, Tuple

from backend.core.analysis.schemas import NormalizedFinding

logger = logging.getLogger("backend.analysis.suppression")


# ---------------------------------------------------------------------------
# Audit record
# ---------------------------------------------------------------------------

class SuppressedFindingRecord:
    """Immutable record of a suppressed finding — retained for audit."""
    __slots__ = ("finding", "suppressor", "reason")

    def __init__(self, finding: NormalizedFinding, suppressor: str, reason: str):
        self.finding    = finding
        self.suppressor = suppressor
        self.reason     = reason

    def to_dict(self) -> dict:
        return {
            "rule_id":    self.finding.rule_id,
            "file_path":  self.finding.file_path,
            "line_number":self.finding.line_number,
            "suppressor": self.suppressor,
            "reason":     self.reason,
        }


# ---------------------------------------------------------------------------
# Base filter contract
# ---------------------------------------------------------------------------

class BaseSuppressionFilter(ABC):
    """
    Abstract base for all suppression filters.

    ``apply`` SHALL be pure (no side-effects) and deterministic.
    It returns two lists: (survivors, suppressed_records).
    """
    name: str = "BaseFilter"

    @abstractmethod
    def apply(
        self,
        findings: List[NormalizedFinding],
    ) -> Tuple[List[NormalizedFinding], List[SuppressedFindingRecord]]:
        ...


# ---------------------------------------------------------------------------
# Built-in filters
# ---------------------------------------------------------------------------

class ConfidenceThresholdFilter(BaseSuppressionFilter):
    """Suppress findings whose static_confidence falls below threshold."""
    name = "ConfidenceThresholdFilter"

    def __init__(self, min_confidence: float = 0.30):
        self.min_confidence = min_confidence

    def apply(
        self, findings: List[NormalizedFinding]
    ) -> Tuple[List[NormalizedFinding], List[SuppressedFindingRecord]]:
        passed, suppressed = [], []
        for f in findings:
            if f.static_confidence >= self.min_confidence:
                passed.append(f)
            else:
                suppressed.append(SuppressedFindingRecord(
                    f, self.name,
                    f"confidence {f.static_confidence:.4f} < threshold {self.min_confidence:.4f}",
                ))
        return passed, suppressed


class ManualSuppressFilter(BaseSuppressionFilter):
    """
    Suppress rule_ids that appear in the project-level suppress list.
    Entries are matched exactly against NormalizedFinding.rule_id.
    """
    name = "ManualSuppressFilter"

    def __init__(self, suppressed_rule_ids: Optional[Set[str]] = None):
        self.suppressed_ids: Set[str] = suppressed_rule_ids or set()

    def add_rule(self, rule_id: str) -> None:
        self.suppressed_ids.add(rule_id)

    def apply(
        self, findings: List[NormalizedFinding]
    ) -> Tuple[List[NormalizedFinding], List[SuppressedFindingRecord]]:
        passed, suppressed = [], []
        for f in findings:
            if f.rule_id in self.suppressed_ids:
                suppressed.append(SuppressedFindingRecord(
                    f, self.name,
                    f"rule_id '{f.rule_id}' is in the manual suppress list",
                ))
            else:
                passed.append(f)
        return passed, suppressed


class DuplicateFilter(BaseSuppressionFilter):
    """
    Deduplicate findings with identical (rule_id, file_path, line_number).
    First occurrence wins; subsequent duplicates are suppressed.
    """
    name = "DuplicateFilter"

    def apply(
        self, findings: List[NormalizedFinding]
    ) -> Tuple[List[NormalizedFinding], List[SuppressedFindingRecord]]:
        seen:  Set[Tuple] = set()
        passed, suppressed = [], []
        for f in findings:
            key = (f.rule_id, f.file_path, f.line_number)
            if key in seen:
                suppressed.append(SuppressedFindingRecord(
                    f, self.name,
                    f"duplicate of ({f.rule_id}, {f.file_path}, L{f.line_number})",
                ))
            else:
                seen.add(key)
                passed.append(f)
        return passed, suppressed


class CommentLineFilter(BaseSuppressionFilter):
    """
    Suppress findings whose flagged line_text is a comment line.
    Supports C/C++ (//, /* … */), Python (#), and shell (#) prefixes.

    Note: A bare `*` only matches C-style block comment continuation lines
    (i.e. `* text`) where the asterisk is followed by whitespace or end-of-line.
    It does NOT match pointer dereferences like `*ptr = 10;`.
    """
    name = "CommentLineFilter"

    # Match: // ...  |  /* ...  |  * <whitespace or eol> (block comment continuation)  |  # ...
    _COMMENT_RE = re.compile(r'^\s*(//|/\*|\*\s|#)')

    def apply(
        self, findings: List[NormalizedFinding]
    ) -> Tuple[List[NormalizedFinding], List[SuppressedFindingRecord]]:
        passed, suppressed = [], []
        for f in findings:
            if self._COMMENT_RE.match(f.line_text):
                suppressed.append(SuppressedFindingRecord(
                    f, self.name,
                    "flagged line is a source-code comment",
                ))
            else:
                passed.append(f)
        return passed, suppressed


class ArchitectureLayerFilter(BaseSuppressionFilter):
    """
    Suppress findings whose file_path falls under a whitelisted directory
    prefix (e.g. ``tests/``, ``vendor/``, ``third_party/``).
    Prefixes are matched case-insensitively on forward-slash-normalised paths.
    """
    name = "ArchitectureLayerFilter"

    def __init__(self, whitelisted_prefixes: Optional[List[str]] = None):
        self.prefixes = [p.lower() for p in (whitelisted_prefixes or [])]

    def apply(
        self, findings: List[NormalizedFinding]
    ) -> Tuple[List[NormalizedFinding], List[SuppressedFindingRecord]]:
        passed, suppressed = [], []
        for f in findings:
            fp = (f.file_path or "").replace("\\", "/").lower()
            hit = next((p for p in self.prefixes if fp.startswith(p)), None)
            if hit:
                suppressed.append(SuppressedFindingRecord(
                    f, self.name,
                    f"file path '{f.file_path}' is under whitelisted prefix '{hit}'",
                ))
            else:
                passed.append(f)
        return passed, suppressed


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class SuppressionPipeline:
    """
    Ordered pipeline of BaseSuppressionFilter instances.

    Usage
    -----
        pipeline = SuppressionPipeline.default()
        result   = pipeline.run(findings)
        # result.passed   → findings to surface
        # result.suppressed → audit records

    The pipeline is deterministic: the same input list processed through the
    same filter configuration MUST produce the same output.
    """

    class Result:
        __slots__ = ("passed", "suppressed")
        def __init__(
            self,
            passed:     List[NormalizedFinding],
            suppressed: List[SuppressedFindingRecord],
        ):
            self.passed     = passed
            self.suppressed = suppressed

    def __init__(self):
        self._filters: List[BaseSuppressionFilter] = []
        self.audit_log: List[SuppressedFindingRecord] = []

    # ------------------------------------------------------------------
    # Configuration API
    # ------------------------------------------------------------------

    def register_filter(self, f: BaseSuppressionFilter) -> "SuppressionPipeline":
        """Append a filter to the end of the pipeline. Returns self for chaining."""
        self._filters.append(f)
        return self

    @classmethod
    def default(
        cls,
        min_confidence:     float           = 0.30,
        manual_suppresses:  Optional[Set[str]]   = None,
        whitelisted_dirs:   Optional[List[str]]  = None,
    ) -> "SuppressionPipeline":
        """
        Factory for the standard HBH suppression configuration.
        Filters are added in cheapest-first order to minimise processing.
        """
        p = cls()
        p.register_filter(ConfidenceThresholdFilter(min_confidence))
        p.register_filter(ManualSuppressFilter(manual_suppresses))
        p.register_filter(DuplicateFilter())
        p.register_filter(CommentLineFilter())
        p.register_filter(ArchitectureLayerFilter(
            whitelisted_dirs or ["tests/", "vendor/", "third_party/", ".git/"]
        ))
        return p

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def run(self, findings: List[NormalizedFinding]) -> "SuppressionPipeline.Result":
        """
        Execute all filters sequentially.  Suppressed findings are accumulated
        in self.audit_log (cleared on each call).
        """
        self.audit_log.clear()
        current = list(findings)

        for fltr in self._filters:
            current, newly_suppressed = fltr.apply(current)
            if newly_suppressed:
                self.audit_log.extend(newly_suppressed)
                logger.debug(
                    "Filter '%s' suppressed %d finding(s).",
                    fltr.name, len(newly_suppressed),
                )

        logger.info(
            "SuppressionPipeline complete: %d passed, %d suppressed.",
            len(current), len(self.audit_log),
        )
        return SuppressionPipeline.Result(
            passed=current,
            suppressed=self.audit_log,
        )
