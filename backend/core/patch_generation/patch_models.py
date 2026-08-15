"""
Patch Generation Data Models  (Phase 3D.1)
===========================================
All Pydantic models that form the public data contract of the Patch
Generation Engine.  Every consumer of the engine—report generators,
downstream validators, history stores—uses these models exclusively.

Design invariants
-----------------
- All models use ``model_config = ConfigDict(frozen=False)`` so objects can
  be mutated during the pipeline (e.g. enriching with diffs after parsing).
- UUIDs are auto-generated for identity fields.
- All timestamp fields use UNIX epoch floats for timezone independence.
- The ``metadata`` dict on composite objects is an open-extension point for
  future phases without requiring a schema migration.

Repair category coverage (20 categories matching §3D.1 spec)
-------------------------------------------------------------
null_pointer_check, memory_leak, dangling_pointer, buffer_overflow,
api_misuse, resource_leak, incorrect_condition, incorrect_loop,
missing_return, exception_handling, boundary_condition,
uninitialized_variable, use_after_free, incorrect_lifetime,
missing_cleanup, incorrect_ownership, stl_misuse, const_correctness,
unsafe_cast, thread_safety
"""
from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class RepairCategory(str, Enum):
    """
    Canonical set of supported bug repair categories.

    Each value maps to a set of specialised repair guidance in the
    ``RepairGuidanceRegistry``.  Adding a new category requires:
      1. Adding the enum member here.
      2. Registering guidance in ``repair_strategies.py``.
    """

    NULL_POINTER_CHECK    = "null_pointer_check"
    MEMORY_LEAK           = "memory_leak"
    DANGLING_POINTER      = "dangling_pointer"
    BUFFER_OVERFLOW       = "buffer_overflow"
    API_MISUSE            = "api_misuse"
    RESOURCE_LEAK         = "resource_leak"
    INCORRECT_CONDITION   = "incorrect_condition"
    INCORRECT_LOOP        = "incorrect_loop"
    MISSING_RETURN        = "missing_return"
    EXCEPTION_HANDLING    = "exception_handling"
    BOUNDARY_CONDITION    = "boundary_condition"
    UNINITIALIZED_VARIABLE= "uninitialized_variable"
    USE_AFTER_FREE        = "use_after_free"
    INCORRECT_LIFETIME    = "incorrect_lifetime"
    MISSING_CLEANUP       = "missing_cleanup"
    INCORRECT_OWNERSHIP   = "incorrect_ownership"
    STL_MISUSE            = "stl_misuse"
    CONST_CORRECTNESS     = "const_correctness"
    UNSAFE_CAST           = "unsafe_cast"
    THREAD_SAFETY         = "thread_safety"
    UNKNOWN               = "unknown"


class RepairStyle(str, Enum):
    """Style directive for patch generation."""
    MINIMAL       = "minimal"       # Absolute minimum change
    CONSERVATIVE  = "conservative"  # Minimal + defensive guards
    STANDARD      = "standard"      # Best practice patterns
    COMPREHENSIVE = "comprehensive" # Thorough + edge cases


class ReasoningMode(str, Enum):
    """LLM reasoning strategy for patch generation."""
    DIRECT      = "direct"       # Single-pass generation
    CHAIN       = "chain"        # Chain-of-thought before code
    STRUCTURED  = "structured"   # JSON-schema-guided output
    MULTI_PASS  = "multi_pass"   # Generate → review → refine


class ActionType(str, Enum):
    """Type of edit action in an edit plan."""
    MODIFY         = "MODIFY"
    ADD_FUNCTION   = "ADD_FUNCTION"
    ADD_INCLUDE    = "ADD_INCLUDE"
    REMOVE         = "REMOVE"
    REFACTOR       = "REFACTOR"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class PatchGenerationConfig(BaseModel):
    """
    Configuration object for the Patch Generation Engine.

    All fields have production-ready defaults that can be overridden at
    construction time.  The config is injected into every component so
    behaviour can be tuned without code changes.
    """

    model_config = ConfigDict(frozen=False)

    # LLM sampling parameters
    temperature:     float = Field(default=0.15, ge=0.0, le=2.0,
                                   description="LLM sampling temperature.")
    top_p:           float = Field(default=0.95, ge=0.0, le=1.0,
                                   description="Nucleus sampling top-p.")
    max_tokens:      int   = Field(default=4096, ge=128,
                                   description="Maximum output tokens per completion.")
    max_prompt_tokens: int = Field(default=12000, ge=512,
                                   description="Maximum *input* tokens for the assembled prompt. "
                                               "Distinct from max_tokens, which bounds the "
                                               "completion the model writes back.")

    # Candidate generation
    candidate_count: int   = Field(default=3, ge=1, le=5,
                                   description="Number of repair candidates to generate.")

    # Repair behaviour
    repair_style:    RepairStyle    = Field(default=RepairStyle.CONSERVATIVE)
    reasoning_mode:  ReasoningMode  = Field(default=ReasoningMode.STRUCTURED)
    strictness:      float          = Field(default=0.7, ge=0.0, le=1.0,
                                           description="0.0 = permissive, 1.0 = very strict.")

    # Context selection
    context_window:  int = Field(default=6000, ge=512,
                                 description="Maximum characters for code context sent to LLM.")

    # Retry policy
    max_retries:     int   = Field(default=3, ge=0,
                                   description="Maximum retry attempts on provider failure.")
    retry_backoff:   float = Field(default=1.5, ge=1.0,
                                   description="Exponential backoff multiplier between retries.")

    # Behaviour flags
    require_guidance:    bool = Field(default=False,
                                      description="If True, raise UnsupportedBugTypeError for unknown categories.")
    preserve_comments:   bool = Field(default=True,
                                      description="Instruct the LLM to preserve existing comments.")
    allow_new_helpers:   bool = Field(default=True,
                                      description="Allow generation of new helper functions.")
    min_candidate_confidence: float = Field(default=0.3, ge=0.0, le=1.0,
                                             description="Discard candidates below this confidence.")


# ---------------------------------------------------------------------------
# Context window
# ---------------------------------------------------------------------------


class ContextWindow(BaseModel):
    """
    Intelligently selected subset of repository context for LLM consumption.

    The context selector constructs this object from the full repository;
    it is designed to stay within the configured token budget while
    maximising repair quality.
    """

    model_config = ConfigDict(frozen=False)

    target_file_content:       str                = Field(default="",
                                                           description="Relevant lines of the target file.")
    target_file_path:          Optional[str]      = Field(default=None)
    target_start_line:         Optional[int]      = Field(default=None)
    target_end_line:           Optional[int]      = Field(default=None)

    neighboring_functions:     List[str]          = Field(default_factory=list,
                                                          description="Bodies of adjacent functions in the same file.")
    referenced_classes:        List[str]          = Field(default_factory=list,
                                                          description="Class definitions referenced by the target function.")
    required_includes:         List[str]          = Field(default_factory=list,
                                                          description="Include directives present in the target file.")
    inheritance_hierarchy:     List[str]          = Field(default_factory=list,
                                                          description="Base class declarations for the target class.")
    call_graph_excerpt:        str                = Field(default="",
                                                          description="Relevant caller/callee paths.")
    data_flow_excerpt:         str                = Field(default="",
                                                          description="DFG dependency chains for affected variables.")
    cfg_nodes:                 List[str]          = Field(default_factory=list,
                                                          description="CFG basic block statements near the bug.")
    rag_documents:             List[str]          = Field(default_factory=list,
                                                          description="Retrieved documentation snippets from RAG.")

    estimated_char_count:      int                = Field(default=0,
                                                          description="Estimated total character count across all fields.")


# ---------------------------------------------------------------------------
# Edit planning
# ---------------------------------------------------------------------------


class EditAction(BaseModel):
    """A single planned edit action within an EditPlan."""

    model_config = ConfigDict(frozen=False)

    action_id:             str       = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    target_file:           str
    target_function:       Optional[str]   = None
    target_class:          Optional[str]   = None
    action_type:           ActionType
    description:           str
    requires_header_update: bool           = False
    include_changes:       List[str]       = Field(default_factory=list,
                                                   description="Includes to add or remove.")
    estimated_lines:       int             = Field(default=0,
                                                   description="Estimated number of lines touched.")
    api_signature_changes: bool            = False
    metadata:              Dict[str, Any]  = Field(default_factory=dict)


class EditPlan(BaseModel):
    """
    Structured edit plan produced before code generation.

    The edit planner analyses the finding, root cause, and context to
    determine the full scope of changes.  This plan is injected into the
    prompt so the LLM generates changes that are consistent with the
    repository structure.
    """

    model_config = ConfigDict(frozen=False)

    plan_id:                str            = Field(default_factory=lambda: str(uuid.uuid4()))
    bug_id:                 str
    repair_category:        RepairCategory
    actions:                List[EditAction] = Field(default_factory=list)

    # High-level flags
    requires_new_helper:    bool           = False
    requires_header_update: bool           = False
    api_changes:            bool           = False
    cross_file_impact:      bool           = False
    expected_side_effects:  List[str]      = Field(default_factory=list)

    created_at:             float          = Field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Patch explanation
# ---------------------------------------------------------------------------


class PatchExplanation(BaseModel):
    """
    Structured developer-facing explanation for a patch candidate.

    Every explanation appears in the engineering report and is designed
    to help code reviewers understand the fix without reading the diff.
    """

    model_config = ConfigDict(frozen=False)

    why_bug_occurred:       str = Field(description="Root cause explanation in developer terms.")
    why_fix_works:          str = Field(description="Mechanism by which the patch resolves the issue.")
    trade_offs:             str = Field(description="Advantages and disadvantages of this approach.")
    complexity_impact:      str = Field(description="Effect on cyclomatic complexity and readability.")
    safety_considerations:  str = Field(description="Thread safety, memory safety, API safety notes.")
    side_effects:           str = Field(description="Possible unintended consequences.")
    limitations:            str = Field(description="Known cases where this fix may be insufficient.")


# ---------------------------------------------------------------------------
# Patch candidate
# ---------------------------------------------------------------------------


class PatchCandidate(BaseModel):
    """
    A single repair candidate produced by the LLM.

    Multiple candidates are generated per bug; downstream phases select
    the best one after validation.  All candidates are preserved in the
    StructuredPatch so the developer can review alternatives.
    """

    model_config = ConfigDict(frozen=False)

    candidate_id:      str       = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    original_code:     str       = Field(description="Exact original code block to be replaced.")
    patched_code:      str       = Field(description="Proposed replacement code block.")
    unified_diff:      str       = Field(default="",
                                         description="Git-compatible unified diff.")
    reasoning:         str       = Field(default="",
                                         description="LLM chain-of-thought reasoning for this candidate.")
    explanation:       Optional[PatchExplanation] = Field(default=None)

    confidence:        float     = Field(default=0.5, ge=0.0, le=1.0,
                                         description="LLM self-assessed confidence in this candidate.")
    advantages:        List[str] = Field(default_factory=list)
    disadvantages:     List[str] = Field(default_factory=list)
    estimated_risk:    float     = Field(default=0.5, ge=0.0, le=1.0)
    preferred_rank:    int       = Field(default=1, description="1 = most preferred.")

    # Style verification
    style_preserved:   bool      = Field(default=True)
    style_violations:  List[str] = Field(default_factory=list)

    metadata:          Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# File patch
# ---------------------------------------------------------------------------


class FilePatch(BaseModel):
    """
    All patch candidates for a single source file.

    A StructuredPatch may contain FilePatch objects for multiple files
    if the bug requires cross-file changes (though minimal patches should
    touch as few files as possible).
    """

    model_config = ConfigDict(frozen=False)

    file_path:      str
    function_name:  Optional[str]          = None
    class_name:     Optional[str]          = None
    start_line:     Optional[int]          = None
    end_line:       Optional[int]          = None

    bug_type:       RepairCategory         = RepairCategory.UNKNOWN
    severity:       str                    = "MEDIUM"
    confidence:     float                  = Field(default=0.5, ge=0.0, le=1.0)

    candidates:     List[PatchCandidate]   = Field(default_factory=list)
    best_candidate_index: int              = Field(default=0,
                                                    description="Index into candidates of the recommended fix.")

    metadata:       Dict[str, Any]         = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Top-level structured patch
# ---------------------------------------------------------------------------


class GenerationMetadata(BaseModel):
    """Runtime metadata recorded during patch generation."""

    model_config = ConfigDict(frozen=False)

    provider:            str
    model:               str          = ""
    prompt_version:      str          = "3D.1.0"
    generation_time_ms:  float        = 0.0
    total_tokens_used:   int          = 0
    retry_count:         int          = 0
    context_chars:       int          = 0
    candidates_requested: int         = 0
    candidates_produced: int          = 0
    reasoning_mode:      str          = ""
    repair_style:        str          = ""
    timestamp:           float        = Field(default_factory=time.time)


class StructuredPatch(BaseModel):
    """
    Top-level patch output returned by ``PatchGenerationEngine.generate()``.

    This is the canonical patch artifact consumed by report generators,
    history stores, and future validation phases (3D.2).

    Multiple file patches are supported for cross-file bug fixes.  The
    ``selected_candidate_index`` identifies the recommended candidate
    across all file patches (each FilePatch has its own
    ``best_candidate_index`` per-file).
    """

    model_config = ConfigDict(frozen=False)

    patch_id:                str                    = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique identifier for this patch artifact.",
    )
    bug_id:                  str
    rule_id:                 str                    = ""
    file_patches:            List[FilePatch]        = Field(default_factory=list)

    # Context summary
    root_cause:              str                    = ""
    evidence_summary:        str                    = ""
    repair_category:         RepairCategory         = RepairCategory.UNKNOWN

    # Warnings and dependency tracking
    warnings:                List[str]              = Field(default_factory=list,
                                                           description="Non-fatal issues (e.g. cross-file impact, partial fix).")
    dependencies:            List[str]              = Field(default_factory=list,
                                                           description="Symbol names or files this patch depends on.")

    # Generation metadata
    generation_metadata:     GenerationMetadata     = Field(
        default_factory=lambda: GenerationMetadata(provider="unknown"),
    )

    created_at:              float                  = Field(default_factory=time.time)
    edit_plan:               Optional[EditPlan]     = None


# ---------------------------------------------------------------------------
# Patch history
# ---------------------------------------------------------------------------


class PatchHistoryEntry(BaseModel):
    """
    Persistent record of a patch generation event for a single bug.

    The history store accumulates these records; future phases will use them
    for continual learning and candidate selection bias.
    """

    model_config = ConfigDict(frozen=False)

    entry_id:            str            = Field(default_factory=lambda: str(uuid.uuid4()))
    bug_id:              str
    rule_id:             str            = ""
    repair_category:     RepairCategory = RepairCategory.UNKNOWN
    provider:            str
    prompt_version:      str            = "3D.1.0"

    # Snapshot of the patch at generation time
    patch_id:            str
    candidate_count:     int
    best_candidate_index: int           = 0
    accepted_patch_id:   Optional[str]  = None  # Set when a human approves a candidate

    generation_time_ms:  float          = 0.0
    timestamp:           float          = Field(default_factory=time.time)

    metadata:            Dict[str, Any] = Field(default_factory=dict)
