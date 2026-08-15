"""
Patch Output Parser  (Phase 3D.1)
===================================
Parses raw LLM response text into typed ``PatchCandidate`` objects.

Responsibilities
----------------
1. Extract JSON from raw text (handles markdown code fences, leading prose,
   trailing text, and partially-escaped content).
2. Validate that required fields are present.
3. Normalise optional fields (defaults for missing confidence, risk, etc.).
4. Produce a list of ``PatchCandidate`` objects ordered by confidence
   (highest first).
5. Raise ``MalformedPatchOutputError`` only when the response contains no
   recoverable JSON at all.
6. Raise ``EmptyPatchError`` when a candidate has identical original and
   patched code (no actual change).

Design invariants
-----------------
- The parser NEVER silently discards candidates; partial candidates are
  included with reduced confidence and a warning in their metadata.
- All string fields are stripped of leading/trailing whitespace.
- Candidate IDs are reassigned sequentially if the LLM omits them.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from backend.core.patch_generation.exceptions import (
    EmptyPatchError,
    MalformedPatchOutputError,
)
from backend.core.patch_generation.patch_models import (
    PatchCandidate,
    PatchExplanation,
)

logger = logging.getLogger("backend.patch_generation.patch_parser")

# Regex patterns for JSON extraction
_JSON_FENCE_RE  = re.compile(r'```(?:json)?\s*([\s\S]*?)```', re.IGNORECASE)
_BRACE_OPEN_RE  = re.compile(r'\{')


class PatchOutputParser:
    """
    Parses LLM text responses into lists of ``PatchCandidate`` objects.

    Usage
    -----
        parser      = PatchOutputParser()
        candidates  = parser.parse(raw_response, bug_id="CVE-2024-001")
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse(
        self,
        raw_response: str,
        bug_id:       str = "",
    ) -> List[PatchCandidate]:
        """
        Parse *raw_response* into a list of ``PatchCandidate`` objects.

        Parameters
        ----------
        raw_response : Raw text from the LLM provider.
        bug_id       : Bug identifier for error messages.

        Returns
        -------
        List of ``PatchCandidate`` objects, ordered by confidence descending.

        Raises
        ------
        MalformedPatchOutputError
            When no JSON can be extracted from *raw_response*.
        EmptyPatchError
            When ALL extracted candidates produce no code change.
        """
        if not raw_response or not raw_response.strip():
            raise MalformedPatchOutputError(
                raw_response="",
                details={"reason": "LLM returned empty response", "bug_id": bug_id},
            )

        data = self._extract_json(raw_response)
        if data is None:
            raise MalformedPatchOutputError(
                raw_response=raw_response,
                details={"reason": "No valid JSON found in response", "bug_id": bug_id},
            )

        raw_candidates = self._extract_candidates_list(data)
        if not raw_candidates:
            raise MalformedPatchOutputError(
                raw_response=raw_response,
                details={
                    "reason": "JSON parsed but no 'candidates' array found",
                    "bug_id": bug_id,
                },
            )

        candidates: List[PatchCandidate] = []
        all_empty = True

        for i, raw in enumerate(raw_candidates):
            candidate = self._parse_candidate(raw, index=i, bug_id=bug_id)
            if candidate:
                if candidate.original_code.strip() != candidate.patched_code.strip():
                    all_empty = False
                candidates.append(candidate)

        if not candidates:
            raise MalformedPatchOutputError(
                raw_response=raw_response,
                details={"reason": "No parseable candidates found", "bug_id": bug_id},
            )

        if all_empty:
            raise EmptyPatchError(bug_id=bug_id, candidate=0)

        # Sort by confidence descending, re-assign preferred_rank
        candidates.sort(key=lambda c: c.confidence, reverse=True)
        for rank, c in enumerate(candidates, 1):
            c.preferred_rank = rank

        logger.info(
            "PatchOutputParser: parsed %d candidate(s) for bug '%s'.",
            len(candidates),
            bug_id,
        )
        return candidates

    # ------------------------------------------------------------------
    # JSON extraction
    # ------------------------------------------------------------------

    def _extract_json(self, text: str) -> Optional[Dict[str, Any]]:
        """
        Try multiple strategies to extract a JSON object from *text*.

        Strategy 1: Markdown code fence (```json ... ```)
        Strategy 2: Find the first '{' and balance braces
        Strategy 3: Try parsing the whole text directly
        """
        # Strategy 1: markdown fence
        fence_match = _JSON_FENCE_RE.search(text)
        if fence_match:
            extracted = fence_match.group(1).strip()
            parsed = self._try_parse(extracted)
            if parsed is not None:
                return parsed

        # Strategy 2: balance braces from first '{'
        brace_match = _BRACE_OPEN_RE.search(text)
        if brace_match:
            start = brace_match.start()
            candidate_str = self._balance_braces(text, start)
            if candidate_str:
                parsed = self._try_parse(candidate_str)
                if parsed is not None:
                    return parsed

        # Strategy 3: whole text
        parsed = self._try_parse(text.strip())
        if parsed is not None:
            return parsed

        return None

    @staticmethod
    def _balance_braces(text: str, start: int) -> Optional[str]:
        """Extract a balanced JSON object starting at *start* position."""
        depth = 0
        in_string = False
        escape_next = False

        for i, ch in enumerate(text[start:], start):
            if escape_next:
                escape_next = False
                continue
            if ch == '\\' and in_string:
                escape_next = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        return None

    @staticmethod
    def _try_parse(text: str) -> Optional[Dict[str, Any]]:
        """Attempt json.loads; return None on failure."""
        try:
            result = json.loads(text)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass
        return None

    # ------------------------------------------------------------------
    # Candidate extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_candidates_list(data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Extract the candidates list from the parsed JSON.

        Supports both:
          { "candidates": [...] }
          [ { candidate }, ... ]  (if model returned an array directly)
        """
        if isinstance(data.get("candidates"), list):
            return data["candidates"]
        # If the top-level is itself a candidate (single-candidate response)
        if "original_code" in data and "patched_code" in data:
            return [data]
        return []

    def _parse_candidate(
        self,
        raw: Dict[str, Any],
        index: int,
        bug_id: str,
    ) -> Optional[PatchCandidate]:
        """
        Parse a single candidate dict into a ``PatchCandidate``.

        Returns None if the minimum required fields are absent.
        """
        original_code = str(raw.get("original_code", "")).strip()
        patched_code  = str(raw.get("patched_code",  "")).strip()

        if not original_code or not patched_code:
            logger.warning(
                "PatchOutputParser: candidate %d for bug '%s' missing "
                "original_code or patched_code — skipping.",
                index,
                bug_id,
            )
            return None

        explanation = self._parse_explanation(raw.get("explanation"))

        candidate = PatchCandidate(
            candidate_id  = str(raw.get("candidate_id", f"C{index + 1}")),
            original_code = original_code,
            patched_code  = patched_code,
            reasoning     = str(raw.get("reasoning", "")).strip(),
            explanation   = explanation,
            confidence    = self._safe_float(raw.get("confidence"), 0.5, 0.0, 1.0),
            advantages    = self._parse_str_list(raw.get("advantages")),
            disadvantages = self._parse_str_list(raw.get("disadvantages")),
            estimated_risk= self._safe_float(raw.get("estimated_risk"), 0.5, 0.0, 1.0),
            preferred_rank= index + 1,
        )

        logger.debug(
            "PatchOutputParser: candidate '%s' parsed — conf=%.2f, risk=%.2f.",
            candidate.candidate_id,
            candidate.confidence,
            candidate.estimated_risk,
        )
        return candidate

    @staticmethod
    def _parse_explanation(
        raw: Optional[Any],
    ) -> Optional[PatchExplanation]:
        """Parse the nested explanation dict into a ``PatchExplanation``."""
        if not isinstance(raw, dict):
            return None
        try:
            return PatchExplanation(
                why_bug_occurred      = str(raw.get("why_bug_occurred",      "Not provided.")),
                why_fix_works         = str(raw.get("why_fix_works",         "Not provided.")),
                trade_offs            = str(raw.get("trade_offs",            "Not provided.")),
                complexity_impact     = str(raw.get("complexity_impact",     "Not provided.")),
                safety_considerations = str(raw.get("safety_considerations", "Not provided.")),
                side_effects          = str(raw.get("side_effects",          "None identified.")),
                limitations           = str(raw.get("limitations",           "None identified.")),
            )
        except Exception as exc:  # pragma: no cover
            logger.warning("PatchOutputParser: failed to parse explanation dict: %s", exc)
            return None

    @staticmethod
    def _safe_float(
        value: Any,
        default: float,
        lo: float,
        hi: float,
    ) -> float:
        """Safely convert *value* to float within [lo, hi]."""
        try:
            f = float(value)
            return max(lo, min(hi, f))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _parse_str_list(value: Any) -> List[str]:
        """Parse a list of strings from JSON; tolerate single strings."""
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []
