"""
Syntax Preserver  (Phase 3D.1)
================================
Analyses the coding style of a source file and validates that generated
patches respect that style, preventing cosmetic edits from contaminating
diffs with unrelated whitespace or formatting changes.

Detected style attributes
--------------------------
- Indentation      : tab-based or space-based, and indent width.
- Brace style      : K&R (opening brace on same line) vs Allman (own line).
- Naming convention: camelCase, snake_case, PascalCase.
- Spacing          : spaces around operators, after keywords.
- Comment style    : line comments (//) vs block comments (/* */).
- Include ordering : system includes before/after local includes.
- Namespace style  : using namespace vs qualified names.

The preserver does NOT modify code that the engine generates — it produces
a ``StyleProfile`` (human-readable summary) that is injected into the
prompt so the LLM generates style-consistent code from the start.

A secondary ``validate_preservation`` method checks that the diff between
original and patched code contains only semantic changes, flagging any
lines that appear to be cosmetic formatting edits.

Design invariants
-----------------
- Style detection is heuristic; it works on a representative sample of
  lines rather than a full parse tree.
- ``validate_preservation`` sets ``PatchCandidate.style_preserved = False``
  and populates ``style_violations`` when cosmetic changes are detected.
- Detection failure is non-fatal; a default profile is returned with a
  warning log.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from backend.core.patch_generation.patch_models import PatchCandidate

logger = logging.getLogger("backend.patch_generation.syntax_preserver")

# Maximum lines to sample for style detection
_SAMPLE_LIMIT = 200


# ---------------------------------------------------------------------------
# Style profile
# ---------------------------------------------------------------------------


@dataclass
class StyleProfile:
    """Detected coding style attributes for a source file."""

    uses_tabs:          bool  = False
    indent_width:       int   = 4
    brace_style:        str   = "kr"           # "kr" or "allman"
    naming_convention:  str   = "snake_case"   # "snake_case", "camelCase", "PascalCase"
    operator_spacing:   bool  = True           # spaces around operators
    comment_style:      str   = "line"         # "line" or "block"
    system_includes_first: bool = True
    uses_namespace:     bool  = False

    def to_prompt_summary(self) -> str:
        """Return a concise, human-readable style summary for LLM injection."""
        indent_desc = (
            f"tabs" if self.uses_tabs
            else f"{self.indent_width}-space indentation"
        )
        return (
            f"Indentation     : {indent_desc}\n"
            f"Brace style     : {'K&R (open brace on same line)' if self.brace_style == 'kr' else 'Allman (open brace on new line)'}\n"
            f"Naming          : {self.naming_convention}\n"
            f"Operator spacing: {'yes' if self.operator_spacing else 'no'}\n"
            f"Comment style   : {'// line comments' if self.comment_style == 'line' else '/* block */ comments'}\n"
            f"Include order   : {'system before local' if self.system_includes_first else 'local before system'}\n"
            f"Namespace       : {'using namespace in scope' if self.uses_namespace else 'fully qualified names preferred'}"
        )


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class SyntaxPreserver:
    """
    Detects the coding style of source code and validates that patches
    preserve that style.

    This engine is stateless; all methods accept the source code as input.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze_style(self, source_code: str) -> StyleProfile:
        """
        Analyse the style of *source_code* and return a ``StyleProfile``.

        Parameters
        ----------
        source_code : Full or representative source file content.

        Returns
        -------
        ``StyleProfile`` with detected attributes.
        Logs a warning if the sample is too short for reliable detection.
        """
        lines = source_code.splitlines()[:_SAMPLE_LIMIT]

        if len(lines) < 5:
            logger.warning(
                "SyntaxPreserver: source too short (%d lines) for reliable "
                "style detection — using defaults.",
                len(lines),
            )
            return StyleProfile()

        profile = StyleProfile(
            uses_tabs          = self._detect_tabs(lines),
            indent_width       = self._detect_indent_width(lines),
            brace_style        = self._detect_brace_style(lines),
            naming_convention  = self._detect_naming(lines),
            operator_spacing   = self._detect_operator_spacing(lines),
            comment_style      = self._detect_comment_style(lines),
            system_includes_first = self._detect_include_order(lines),
            uses_namespace     = self._detect_namespace(lines),
        )

        logger.debug(
            "SyntaxPreserver: detected style — indent=%s%d, brace=%s, naming=%s.",
            "tab" if profile.uses_tabs else "space",
            profile.indent_width,
            profile.brace_style,
            profile.naming_convention,
        )
        return profile

    def validate_preservation(
        self,
        candidates: List[PatchCandidate],
        profile:    StyleProfile,
    ) -> List[PatchCandidate]:
        """
        Check each candidate for cosmetic (non-semantic) changes.

        Sets ``candidate.style_preserved = False`` and populates
        ``candidate.style_violations`` for any cosmetic edits detected.

        Returns the (mutated) candidates list.
        """
        for candidate in candidates:
            violations = self._check_candidate(candidate, profile)
            if violations:
                candidate.style_preserved  = False
                candidate.style_violations = violations
                logger.warning(
                    "SyntaxPreserver: candidate '%s' has %d style violation(s): %s",
                    candidate.candidate_id,
                    len(violations),
                    "; ".join(violations[:3]),
                )
            else:
                candidate.style_preserved  = True
                candidate.style_violations = []

        return candidates

    # ------------------------------------------------------------------
    # Style detection helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_tabs(lines: List[str]) -> bool:
        tab_count   = sum(1 for l in lines if l.startswith("\t"))
        space_count = sum(1 for l in lines if l.startswith("    "))
        return tab_count > space_count

    @staticmethod
    def _detect_indent_width(lines: List[str]) -> int:
        """Return the most common leading-space indent width (2 or 4)."""
        indent_sizes: List[int] = []
        for line in lines:
            stripped = line.lstrip(' ')
            spaces   = len(line) - len(stripped)
            if spaces > 0 and spaces % 2 == 0:
                indent_sizes.append(spaces)
        if not indent_sizes:
            return 4
        # Return the GCD-like minimum common factor (2 or 4)
        counts = {2: 0, 4: 0}
        for s in indent_sizes:
            if s % 4 == 0:
                counts[4] += 1
            elif s % 2 == 0:
                counts[2] += 1
        return 4 if counts[4] >= counts[2] else 2

    @staticmethod
    def _detect_brace_style(lines: List[str]) -> str:
        """Detect K&R vs Allman by counting opening-brace line positions."""
        kr     = 0  # brace at end of function/control-flow line
        allman = 0  # brace on its own line

        brace_own_line = re.compile(r'^\s*\{')
        for line in lines:
            stripped = line.rstrip()
            if brace_own_line.match(line) and stripped.strip() == '{':
                allman += 1
            elif stripped.endswith('{') and not stripped.strip() == '{':
                kr += 1

        return "allman" if allman > kr else "kr"

    @staticmethod
    def _detect_naming(lines: List[str]) -> str:
        """Heuristically determine naming convention from function names."""
        camel   = 0
        snake   = 0
        pascal  = 0
        func_re = re.compile(r'\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\(')
        for line in lines:
            for name in func_re.findall(line):
                if '_' in name:
                    snake += 1
                elif name[0].isupper():
                    pascal += 1
                elif any(c.isupper() for c in name[1:]):
                    camel += 1
        dominant = max(snake, camel, pascal)
        if dominant == snake:
            return "snake_case"
        if dominant == pascal:
            return "PascalCase"
        return "camelCase"

    @staticmethod
    def _detect_operator_spacing(lines: List[str]) -> bool:
        """Check whether operators are surrounded by spaces."""
        spaced     = 0
        not_spaced = 0
        op_re      = re.compile(r'(\w)(==|!=|<=|>=|&&|\|\|)(\w)')
        spaced_re  = re.compile(r'\w\s+(==|!=|<=|>=|&&|\|\|)\s+\w')
        for line in lines:
            if op_re.search(line):
                not_spaced += 1
            if spaced_re.search(line):
                spaced += 1
        return spaced >= not_spaced

    @staticmethod
    def _detect_comment_style(lines: List[str]) -> str:
        line_comments  = sum(1 for l in lines if '//' in l)
        block_comments = sum(1 for l in lines if '/*' in l or '*/' in l)
        return "block" if block_comments > line_comments else "line"

    @staticmethod
    def _detect_include_order(lines: List[str]) -> bool:
        """
        Return True if system includes (<...>) appear before local (<"...">) ones.
        """
        last_system = -1
        first_local = float('inf')
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith('#include <'):
                last_system = i
            elif stripped.startswith('#include "'):
                first_local = min(first_local, i)
        return last_system < first_local

    @staticmethod
    def _detect_namespace(lines: List[str]) -> bool:
        return any('using namespace' in l for l in lines)

    # ------------------------------------------------------------------
    # Style validation helpers
    # ------------------------------------------------------------------

    def _check_candidate(
        self,
        candidate: PatchCandidate,
        profile:   StyleProfile,
    ) -> List[str]:
        """Return list of style violation messages for this candidate."""
        violations: List[str] = []

        orig_lines    = candidate.original_code.splitlines()
        patched_lines = candidate.patched_code.splitlines()

        # Check that indentation style wasn't changed
        orig_tabs    = any(l.startswith('\t') for l in orig_lines)
        patched_tabs = any(l.startswith('\t') for l in patched_lines)
        if orig_tabs != patched_tabs:
            violations.append(
                "Indentation style changed (tabs vs spaces)."
            )

        # Warn if brace style was changed
        orig_allman    = any(re.match(r'^\s*\{', l) and l.strip() == '{' for l in orig_lines)
        patched_allman = any(re.match(r'^\s*\{', l) and l.strip() == '{' for l in patched_lines)
        if orig_allman != patched_allman:
            violations.append(
                "Brace placement style changed."
            )

        # Check for excessive whitespace-only changed lines
        import difflib as dl
        diff = list(dl.ndiff(orig_lines, patched_lines))
        whitespace_only_changes = 0
        for line in diff:
            if line.startswith('- ') or line.startswith('+ '):
                code = line[2:]
                if code.strip() == '':
                    whitespace_only_changes += 1
        if whitespace_only_changes > 3:
            violations.append(
                f"{whitespace_only_changes} whitespace-only changed lines detected "
                "(possible cosmetic edits)."
            )

        return violations
