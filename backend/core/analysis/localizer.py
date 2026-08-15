"""
Bug Localization Engine  (§113)
=================================
Maps every NormalizedFinding to the most precise source location(s) available,
producing a ranked list of CodeSpan candidates.

Localization strategy (cheapest to most expensive):
1. Use finding.line_number directly if present → STATEMENT granularity.
2. Resolve via RepositoryKnowledgeGraph symbol lookup → FUNCTION granularity.
3. Fall back to file-level span → FILE granularity.

All candidates are ranked by localization_confidence descending.
"""
import logging
from dataclasses import dataclass, field
from typing import List, Literal, Optional

from backend.core.analysis.repo_graph import RepositoryKnowledgeGraph
from backend.core.analysis.schemas import NormalizedFinding

logger = logging.getLogger("backend.analysis.localizer")

Granularity = Literal["STATEMENT", "FUNCTION", "CLASS", "MODULE", "FILE"]


@dataclass
class CodeSpan:
    """A resolved source location for a finding."""
    file_path: str
    start_line: Optional[int]
    end_line:   Optional[int]
    granularity: Granularity
    localization_confidence: float
    symbol_name: Optional[str] = None

    def __repr__(self) -> str:
        loc = f"L{self.start_line}" if self.start_line else "?"
        return f"CodeSpan({self.granularity}, {self.file_path}:{loc}, conf={self.localization_confidence:.2f})"


class BugLocalizer:
    """
    Maps a NormalizedFinding to a ranked list of CodeSpan candidates.

    The localizer is intentionally stateless; callers pass the repository
    graph as an argument so the engine is safe for concurrent use.
    """

    def locate(
        self,
        finding: NormalizedFinding,
        graph: RepositoryKnowledgeGraph,
    ) -> List[CodeSpan]:
        """
        Return candidate CodeSpans ranked by localization_confidence descending.

        Strategy
        --------
        1. STATEMENT — use finding.file_path + finding.line_number directly.
        2. FUNCTION  — look up symbols defined in the same file; find the
                        symbol whose declaration line is closest to (but not
                        after) the finding's line_number.
        3. FILE      — fallback when no line info is available.
        """
        candidates: List[CodeSpan] = []

        # --- Stage 1: direct line mapping -----------------------------------
        if finding.file_path and finding.line_number is not None:
            candidates.append(CodeSpan(
                file_path=finding.file_path,
                start_line=finding.line_number,
                end_line=finding.line_number,
                granularity="STATEMENT",
                localization_confidence=min(finding.static_confidence + 0.10, 1.0),
            ))

        # --- Stage 2: symbol resolution in same file ------------------------
        if finding.file_path and finding.line_number is not None:
            file_symbols = [
                s for s in graph.symbols.values()
                if s.file_path == finding.file_path
                and s.symbol_type in ("function", "class")
                and s.line_number <= finding.line_number
            ]
            if file_symbols:
                # Closest symbol above the finding line
                closest = max(file_symbols, key=lambda s: s.line_number)
                candidates.append(CodeSpan(
                    file_path=finding.file_path,
                    start_line=closest.line_number,
                    end_line=None,
                    granularity="FUNCTION" if closest.symbol_type == "function" else "CLASS",
                    localization_confidence=min(finding.static_confidence + 0.05, 1.0),
                    symbol_name=closest.name,
                ))

        # --- Stage 3: file-level fallback -----------------------------------
        if finding.file_path:
            candidates.append(CodeSpan(
                file_path=finding.file_path,
                start_line=None,
                end_line=None,
                granularity="FILE",
                localization_confidence=finding.static_confidence * 0.60,
            ))

        candidates.sort(key=lambda c: c.localization_confidence, reverse=True)
        logger.debug(
            "BugLocalizer produced %d candidate(s) for rule '%s'.",
            len(candidates), finding.rule_id,
        )
        return candidates
