"""
Patch Generation Exception Hierarchy  (Phase 3D.1)
====================================================
Defines a structured, named exception hierarchy covering every failure mode
the Patch Generation Engine can encounter.

Design invariants
-----------------
- Every exception subclasses ``PatchGenerationError`` so callers can catch
  the base type for general protection without losing specificity.
- All exceptions accept an optional ``details`` dict for structured metadata
  that can be logged or surfaced to the API without exposing raw tracebacks.
- No exception swallows information: the original ``cause`` (if any) is
  always chained via ``raise ... from cause``.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class PatchGenerationError(RuntimeError):
    """
    Base class for all patch generation failures.

    Attributes
    ----------
    message : Human-readable error description.
    details : Optional structured metadata for logging / API responses.
    """

    def __init__(
        self,
        message: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details: Dict[str, Any] = details or {}

    def __repr__(self) -> str:  # pragma: no cover
        return f"{self.__class__.__name__}({self.message!r})"


# ---------------------------------------------------------------------------
# Provider errors
# ---------------------------------------------------------------------------


class ProviderUnavailableError(PatchGenerationError):
    """
    Raised when the configured LLM provider is unreachable, times out, or
    returns an HTTP error that cannot be retried.

    Parameters
    ----------
    provider : Name of the provider that failed (e.g. "groq", "openai").
    cause    : Original exception from the provider SDK.
    """

    def __init__(
        self,
        provider: str,
        cause: Optional[Exception] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(
            f"LLM provider '{provider}' is unavailable.",
            details={**(details or {}), "provider": provider},
        )
        self.provider = provider
        self.__cause__ = cause


class RetryExhaustedError(PatchGenerationError):
    """
    Raised when all configured retry attempts have been exhausted without a
    successful LLM response.

    Parameters
    ----------
    attempts : Total number of attempts made.
    provider : Name of the provider that was retried.
    """

    def __init__(
        self,
        attempts: int,
        provider: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(
            f"Retry limit exhausted after {attempts} attempt(s) "
            f"on provider '{provider}'.",
            details={**(details or {}), "attempts": attempts, "provider": provider},
        )
        self.attempts = attempts
        self.provider = provider


# ---------------------------------------------------------------------------
# Output parsing errors
# ---------------------------------------------------------------------------


class MalformedPatchOutputError(PatchGenerationError):
    """
    Raised when the LLM returns a response that cannot be parsed into a
    valid ``PatchCandidate`` (e.g., invalid JSON, missing required fields,
    structurally inconsistent diff).

    Parameters
    ----------
    raw_response : The raw LLM output string that failed parsing.
    """

    def __init__(
        self,
        raw_response: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(
            "LLM patch output is malformed and could not be parsed.",
            details={
                **(details or {}),
                "raw_response_preview": raw_response[:300],
            },
        )
        self.raw_response = raw_response


class EmptyPatchError(PatchGenerationError):
    """
    Raised when the LLM produces a structurally valid response but the
    patch contains no code changes (original_code == patched_code or the
    diff is empty).

    Parameters
    ----------
    bug_id    : Identifier of the bug for which the patch was requested.
    candidate : Index of the candidate that was empty (0-based).
    """

    def __init__(
        self,
        bug_id: str,
        candidate: int = 0,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(
            f"Patch candidate {candidate} for bug '{bug_id}' contains no code changes.",
            details={**(details or {}), "bug_id": bug_id, "candidate": candidate},
        )
        self.bug_id = bug_id
        self.candidate = candidate


# ---------------------------------------------------------------------------
# Context and prompt errors
# ---------------------------------------------------------------------------


class ContextOverflowError(PatchGenerationError):
    """
    Raised when the assembled context window exceeds the configured token
    budget, making it impossible to construct a prompt within the model's
    context length.

    Parameters
    ----------
    estimated_tokens : Estimated token count of the assembled context.
    budget           : Maximum allowed tokens.
    """

    def __init__(
        self,
        estimated_tokens: int,
        budget: int,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(
            f"Context window overflow: {estimated_tokens} tokens estimated "
            f"(budget: {budget}).",
            details={
                **(details or {}),
                "estimated_tokens": estimated_tokens,
                "budget": budget,
            },
        )
        self.estimated_tokens = estimated_tokens
        self.budget = budget


class PromptOverflowError(PatchGenerationError):
    """
    Raised when the final assembled prompt (system + user) exceeds the
    provider's max_tokens limit even after context truncation.

    Parameters
    ----------
    prompt_tokens : Estimated token count of the assembled prompt.
    max_tokens    : Provider limit.
    """

    def __init__(
        self,
        prompt_tokens: int,
        max_tokens: int,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(
            f"Prompt length {prompt_tokens} tokens exceeds provider "
            f"max_tokens={max_tokens}.",
            details={
                **(details or {}),
                "prompt_tokens": prompt_tokens,
                "max_tokens": max_tokens,
            },
        )
        self.prompt_tokens = prompt_tokens
        self.max_tokens = max_tokens


# ---------------------------------------------------------------------------
# Bug type and input errors
# ---------------------------------------------------------------------------


class UnsupportedBugTypeError(PatchGenerationError):
    """
    Raised when the identified repair category has no registered repair
    guidance and the engine is configured to require guidance (strict mode).

    Parameters
    ----------
    bug_type : The ``RepairCategory`` value that has no guidance entry.
    """

    def __init__(
        self,
        bug_type: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(
            f"No repair guidance registered for bug type '{bug_type}'.",
            details={**(details or {}), "bug_type": bug_type},
        )
        self.bug_type = bug_type


class MissingContextError(PatchGenerationError):
    """
    Raised when required context (e.g., source code, file path) is absent
    and the engine cannot proceed with patch generation.

    Parameters
    ----------
    missing_field : Name of the missing required context field.
    """

    def __init__(
        self,
        missing_field: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(
            f"Required context field '{missing_field}' is absent.",
            details={**(details or {}), "missing_field": missing_field},
        )
        self.missing_field = missing_field
