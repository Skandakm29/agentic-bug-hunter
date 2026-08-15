"""
Patch Generation Package  (Phase 3D.1)
========================================
Public API surface for the Autonomous Patch Generation Engine.

Primary exports
---------------
- ``PatchGenerationEngine``  — Main entry point; accepts a bug report and
                                produces a ``StructuredPatch``.
- ``PatchGenerationConfig``  — Configuration model for all engine parameters.
- ``StructuredPatch``        — Top-level patch output artifact.
- ``RepairCategory``         — Enum of 20 supported bug categories.
- ``PatchHistoryStore``      — Shared patch generation history store.

Quick start
-----------
    from backend.core.ai.providers.factory import LLMProviderFactory
    from backend.core.patch_generation import (
        PatchGenerationEngine,
        PatchGenerationConfig,
    )

    provider = LLMProviderFactory.get_provider("groq")
    engine   = PatchGenerationEngine(provider)

    patch = await engine.generate(
        finding    = finding,          # NormalizedFinding
        code       = source_code,      # str
        root_cause = root_cause_chain, # RootCauseChain
        evidence   = evidence_graph,   # EvidenceGraph
        bug_id     = "CVE-2024-001",
    )
    print(patch.patch_id, len(patch.file_patches))
"""
from backend.core.patch_generation.patch_generator import PatchGenerationEngine
from backend.core.patch_generation.patch_models import (
    PatchGenerationConfig,
    PatchCandidate,
    PatchExplanation,
    FilePatch,
    StructuredPatch,
    EditPlan,
    EditAction,
    ContextWindow,
    RepairCategory,
    RepairStyle,
    ReasoningMode,
    GenerationMetadata,
    PatchHistoryEntry,
    ActionType,
)
from backend.core.patch_generation.patch_history import PatchHistoryStore
from backend.core.patch_generation.repair_strategies import RepairGuidanceRegistry
from backend.core.patch_generation.exceptions import (
    PatchGenerationError,
    ProviderUnavailableError,
    RetryExhaustedError,
    MalformedPatchOutputError,
    EmptyPatchError,
    ContextOverflowError,
    PromptOverflowError,
    UnsupportedBugTypeError,
    MissingContextError,
)

__all__ = [
    # Engine
    "PatchGenerationEngine",
    # Config & models
    "PatchGenerationConfig",
    "PatchCandidate",
    "PatchExplanation",
    "FilePatch",
    "StructuredPatch",
    "EditPlan",
    "EditAction",
    "ContextWindow",
    "RepairCategory",
    "RepairStyle",
    "ReasoningMode",
    "GenerationMetadata",
    "PatchHistoryEntry",
    "ActionType",
    # History
    "PatchHistoryStore",
    # Guidance
    "RepairGuidanceRegistry",
    # Exceptions
    "PatchGenerationError",
    "ProviderUnavailableError",
    "RetryExhaustedError",
    "MalformedPatchOutputError",
    "EmptyPatchError",
    "ContextOverflowError",
    "PromptOverflowError",
    "UnsupportedBugTypeError",
    "MissingContextError",
]
