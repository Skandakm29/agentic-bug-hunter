"""
Unified Diff Generator  (Phase 3D.1)
======================================
Generates Git-compatible unified diffs from (original_code, patched_code)
pairs, suitable for both human review and automated patch application in
future Phase 3D.2.

Output format
-------------
    --- a/<file_path>
    +++ b/<file_path>
    @@ -<start>,<count> +<start>,<count> @@
    -removed line
    +added line
     unchanged context line

Design invariants
-----------------
- Context lines: 3 (Git default); configurable at construction.
- File paths in headers default to "original" / "patched" when unknown.
- Empty diffs (identical original and patched) are returned as empty strings
  rather than raising — callers check for emptiness.
- Line endings are normalised to LF before diffing to avoid false differences
  caused by CRLF vs LF inconsistencies.
- The generator is stateless and thread-safe.
"""
from __future__ import annotations

import difflib
import logging
import re
from typing import List, Optional

from backend.core.patch_generation.patch_models import PatchCandidate

logger = logging.getLogger("backend.patch_generation.diff_generator")

_DEFAULT_CONTEXT_LINES = 3


class UnifiedDiffGenerator:
    """
    Generates unified diffs for patch candidates.

    Parameters
    ----------
    context_lines : Number of unchanged context lines surrounding each hunk.
    """

    def __init__(self, context_lines: int = _DEFAULT_CONTEXT_LINES) -> None:
        self._context_lines = context_lines

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(
        self,
        original_code: str,
        patched_code:  str,
        file_path:     Optional[str] = None,
        start_line:    int           = 1,
    ) -> str:
        """
        Generate a unified diff between *original_code* and *patched_code*.

        Parameters
        ----------
        original_code : Code before the patch.
        patched_code  : Code after the patch.
        file_path     : Source file path for diff headers (optional).
        start_line    : First line number of the original code region (for
                        accurate @@ annotations).

        Returns
        -------
        Unified diff string.  Empty string if the codes are identical.
        """
        # Normalise line endings
        orig_lines    = self._normalise(original_code)
        patched_lines = self._normalise(patched_code)

        if orig_lines == patched_lines:
            logger.debug("UnifiedDiffGenerator: no changes detected — empty diff.")
            return ""

        from_file = f"a/{file_path}" if file_path else "a/original"
        to_file   = f"b/{file_path}" if file_path else "b/patched"

        diff = list(difflib.unified_diff(
            orig_lines,
            patched_lines,
            fromfile    = from_file,
            tofile      = to_file,
            n           = self._context_lines,
            lineterm    = "",
        ))

        if not diff:
            return ""

        # Adjust @@ line numbers by start_line offset
        diff_str = self._adjust_line_numbers(diff, start_line - 1)

        logger.debug(
            "UnifiedDiffGenerator: produced %d-line diff for '%s'.",
            len(diff),
            file_path or "unknown",
        )
        return diff_str

    def generate_from_candidates(
        self,
        candidates: List[PatchCandidate],
        file_path:  Optional[str] = None,
        start_line: int           = 1,
    ) -> List[PatchCandidate]:
        """
        Populate the ``unified_diff`` field on each candidate in-place.

        Returns the mutated candidates list (same objects, diffs filled in).
        """
        for candidate in candidates:
            diff = self.generate(
                candidate.original_code,
                candidate.patched_code,
                file_path  = file_path,
                start_line = start_line,
            )
            candidate.unified_diff = diff

        return candidates

    def generate_summary_diff(
        self,
        candidates: List[PatchCandidate],
        file_path:  Optional[str] = None,
        start_line: int           = 1,
    ) -> str:
        """
        Return a combined diff showing all candidates separated by markers.
        Useful for presenting all repair options in a single diff view.
        """
        parts: List[str] = []
        for c in candidates:
            diff = self.generate(
                c.original_code,
                c.patched_code,
                file_path  = file_path,
                start_line = start_line,
            )
            if diff:
                parts.append(
                    f"# Candidate {c.preferred_rank} "
                    f"(id={c.candidate_id}, confidence={c.confidence:.2f})\n"
                    + diff
                )
        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise(code: str) -> List[str]:
        """Normalise line endings and return a list of lines (with newlines)."""
        normalised = code.replace("\r\n", "\n").replace("\r", "\n")
        lines      = normalised.splitlines(keepends=True)
        # Ensure last line ends with newline
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        return lines

    @staticmethod
    def _adjust_line_numbers(diff_lines: List[str], offset: int) -> str:
        """
        Shift @@ hunk line numbers by *offset* to account for context start.

        The ``difflib.unified_diff`` output uses 1-indexed positions within
        the supplied code strings; we add *offset* to convert to file-level
        line numbers.
        """
        if offset == 0:
            return "\n".join(diff_lines)

        result: List[str] = []
        hunk_re = re.compile(
            r'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)',
        )
        for line in diff_lines:
            m = hunk_re.match(line)
            if m:
                old_start = int(m.group(1)) + offset
                old_count = m.group(2)
                new_start = int(m.group(3)) + offset
                new_count = m.group(4)
                tail      = m.group(5)
                old_part  = f"{old_start},{old_count}" if old_count else str(old_start)
                new_part  = f"{new_start},{new_count}" if new_count else str(new_start)
                result.append(f"@@ -{old_part} +{new_part} @@{tail}")
            else:
                result.append(line)

        return "\n".join(result)
