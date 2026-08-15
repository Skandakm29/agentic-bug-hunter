"""
Patch Generation Engine  (Phase 3D.1)
=======================================
Top-level orchestrator of the autonomous patch generation pipeline.
This is the single public entry point that external callers (e.g.,
``DetectionOrchestrator``) use to request a patch.

Pipeline sequence
-----------------
    1. validate_inputs          — Guard against missing required data.
    2. classify_bug_category    — Map rule_id / description to RepairCategory.
    3. context_selector.select  — Build a token-bounded ContextWindow.
    4. edit_planner.plan        — Build an EditPlan.
    5. syntax_preserver.analyze_style — Detect coding style.
    6. prompt_builder.build     — Assemble system + user prompt.
    7. _invoke_with_retry       — Call LLM provider with retries.
    8. patch_parser.parse       — Parse LLM output into PatchCandidates.
    9. diff_generator.generate  — Generate unified diffs.
   10. patch_explainer.explain  — Populate PatchExplanations.
   11. syntax_preserver.validate— Check style preservation.
   12. patch_builder.build      — Assemble StructuredPatch.
   13. patch_history.record     — Persist to history store.
   14. return StructuredPatch   — Deliver to caller.

Error handling
--------------
- ``ProviderUnavailableError`` → retried up to max_retries times.
- ``MalformedPatchOutputError`` → retried (model may self-correct).
- ``EmptyPatchError`` → retried (model may produce a non-empty patch).
- ``ContextOverflowError`` → not retried; raised immediately.
- ``PromptOverflowError`` → not retried; raised immediately.
- Any unrecoverable error: logged + propagated to the caller.
- Retry exhaustion → ``RetryExhaustedError`` with attempt count.

Design invariants
-----------------
- All side effects (LLM calls, history writes) are isolated from the
  pipeline logic so unit tests can mock providers transparently.
- The engine is async-first: ``generate()`` is an async method matching
  the existing ``BaseLLMProvider.generate_completion_async()`` contract.
- Provider independence: the engine depends only on ``BaseLLMProvider``.
- All timings are recorded for observability.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import List, Optional

from backend.core.ai.providers.base import BaseLLMProvider
from backend.core.analysis.cfg import CFGGraph
from backend.core.analysis.dfg import DataDependency
from backend.core.analysis.evidence import EvidenceGraph
from backend.core.analysis.repo_graph import RepositoryKnowledgeGraph
from backend.core.analysis.root_cause import RootCauseChain
from backend.core.analysis.schemas import NormalizedFinding

from backend.core.patch_generation.context_selector import ContextSelector
from backend.core.patch_generation.diff_generator import UnifiedDiffGenerator
from backend.core.patch_generation.edit_planner import EditPlanner
from backend.core.patch_generation.exceptions import (
    ContextOverflowError,
    MalformedPatchOutputError,
    EmptyPatchError,
    MissingContextError,
    PatchGenerationError,
    PromptOverflowError,
    ProviderUnavailableError,
    RetryExhaustedError,
)
from backend.core.patch_generation.patch_builder import PatchBuilder
from backend.core.patch_generation.patch_explainer import PatchExplainerEngine
from backend.core.patch_generation.patch_history import PatchHistoryStore
from backend.core.patch_generation.patch_models import (
    GenerationMetadata,
    PatchGenerationConfig,
    PatchHistoryEntry,
    RepairCategory,
    StructuredPatch,
)
from backend.core.patch_generation.patch_parser import PatchOutputParser
from backend.core.patch_generation.prompt_builder import PatchPromptBuilder
from backend.core.patch_generation.repair_strategies import RepairGuidanceRegistry
from backend.core.patch_generation.syntax_preserver import SyntaxPreserver

logger = logging.getLogger("backend.patch_generation.patch_generator")

# ---------------------------------------------------------------------------
# Bug category classifier (keyword-based heuristic)
# ---------------------------------------------------------------------------

_CATEGORY_KEYWORDS: dict[RepairCategory, List[str]] = {
    RepairCategory.NULL_POINTER_CHECK:    ["null", "nullptr", "null_pointer", "npe", "null_deref"],
    RepairCategory.MEMORY_LEAK:           ["memory_leak", "leak", "malloc", "new", "alloc", "free"],
    RepairCategory.DANGLING_POINTER:      ["dangling", "stale", "freed_pointer"],
    RepairCategory.BUFFER_OVERFLOW:       ["buffer_overflow", "overflow", "out_of_bounds", "bounds"],
    RepairCategory.API_MISUSE:            ["api_misuse", "invalid_call", "rdi", "suspicious_method"],
    RepairCategory.RESOURCE_LEAK:         ["resource_leak", "file_leak", "handle", "fd_leak"],
    RepairCategory.INCORRECT_CONDITION:   ["incorrect_condition", "wrong_condition", "always_true", "always_false"],
    RepairCategory.INCORRECT_LOOP:        ["infinite_loop", "loop_bound", "off_by_one"],
    RepairCategory.MISSING_RETURN:        ["missing_return", "no_return", "fallthrough"],
    RepairCategory.EXCEPTION_HANDLING:    ["exception", "throw", "unhandled", "noexcept"],
    RepairCategory.BOUNDARY_CONDITION:    ["boundary", "empty_container", "division_by_zero", "zero_div"],
    RepairCategory.UNINITIALIZED_VARIABLE:["uninit", "uninitialized", "used_before_assign"],
    RepairCategory.USE_AFTER_FREE:        ["use_after_free", "uaf", "heap_use_after_free"],
    RepairCategory.INCORRECT_LIFETIME:    ["lifetime", "dangling_reference", "temporary"],
    RepairCategory.MISSING_CLEANUP:       ["missing_cleanup", "cleanup", "destructor"],
    RepairCategory.INCORRECT_OWNERSHIP:   ["ownership", "double_free", "double_delete"],
    RepairCategory.STL_MISUSE:            ["stl", "iterator", "invalidated", "vector", "map"],
    RepairCategory.CONST_CORRECTNESS:     ["const", "mutable", "const_cast"],
    RepairCategory.UNSAFE_CAST:           ["cast", "reinterpret", "c_style_cast", "static_cast"],
    RepairCategory.THREAD_SAFETY:         ["race", "thread", "atomic", "mutex", "tsan", "blocking_in_isr"],
}


def _classify_category(finding: NormalizedFinding) -> RepairCategory:
    """
    Map a ``NormalizedFinding`` to the most specific ``RepairCategory``
    using keyword matching on rule_id and description.
    """
    text = (finding.rule_id + " " + finding.description).lower()
    best_category = RepairCategory.UNKNOWN
    best_score    = 0

    for category, keywords in _CATEGORY_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text)
        if score > best_score:
            best_score    = score
            best_category = category

    if best_category == RepairCategory.UNKNOWN:
        logger.debug(
            "PatchGenerationEngine: could not classify rule '%s' — using UNKNOWN.",
            finding.rule_id,
        )

    return best_category


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class PatchGenerationEngine:
    """
    Autonomous Patch Generation Engine.

    Accepts validated bug reports and repository context, orchestrates the
    full generation pipeline, and returns a ``StructuredPatch``.

    Parameters
    ----------
    provider    : Any ``BaseLLMProvider`` implementation (Groq, OpenAI, etc.).
    config      : ``PatchGenerationConfig`` with all tunable parameters.
    graph       : Optional pre-populated ``RepositoryKnowledgeGraph``.
    history     : Optional shared ``PatchHistoryStore`` (shared across calls).
    registry    : Optional ``RepairGuidanceRegistry`` (shared across calls).
    """

    _PROMPT_VERSION = "3D.1.0"

    def __init__(
        self,
        provider:  BaseLLMProvider,
        config:    Optional[PatchGenerationConfig]      = None,
        graph:     Optional[RepositoryKnowledgeGraph]   = None,
        history:   Optional[PatchHistoryStore]          = None,
        registry:  Optional[RepairGuidanceRegistry]     = None,
    ) -> None:
        self._provider = provider
        self._config   = config   if config is not None else PatchGenerationConfig()
        self._graph    = graph    if graph is not None else RepositoryKnowledgeGraph()
        self._history  = history  if history is not None else PatchHistoryStore()
        self._registry = registry if registry is not None else RepairGuidanceRegistry()

        # Sub-components (injected for testability)
        self._context_selector = ContextSelector(self._config)
        self._edit_planner     = EditPlanner(self._config)
        self._prompt_builder   = PatchPromptBuilder(self._config, self._registry)
        self._patch_parser     = PatchOutputParser()
        self._diff_generator   = UnifiedDiffGenerator()
        self._syntax_preserver = SyntaxPreserver()
        self._patch_explainer  = PatchExplainerEngine(
            self._config, self._registry, self._provider
        )
        self._patch_builder    = PatchBuilder(self._config)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def generate(
        self,
        finding:   NormalizedFinding,
        code:      str,
        root_cause: Optional[RootCauseChain]  = None,
        evidence:  Optional[EvidenceGraph]    = None,
        dfg_deps:  Optional[List[DataDependency]] = None,
        cfg_graph: Optional[CFGGraph]         = None,
        bug_id:    str                        = "",
        coding_guidelines: str               = "",
    ) -> StructuredPatch:
        """
        Generate a structured patch for *finding*.

        Parameters
        ----------
        finding            : The confirmed ``NormalizedFinding`` to repair.
        code               : Full source code of the target file.
        root_cause         : Root cause chain (from ``RootCauseAnalyzer``).
        evidence           : Evidence graph (from ``EvidenceGraphBuilder``).
        dfg_deps           : DFG dependency list (for context selection).
        cfg_graph          : CFG graph (for context selection).
        bug_id             : Opaque identifier for this bug.
        coding_guidelines  : Optional organisation-specific coding standards.

        Returns
        -------
        A ``StructuredPatch`` with all candidates, diffs, and explanations.

        Raises
        ------
        MissingContextError      — required input fields are absent.
        ContextOverflowError     — context is too large for the token budget.
        RetryExhaustedError      — all retry attempts failed.
        PatchGenerationError     — unrecoverable pipeline failure.
        """
        t_start = time.perf_counter()
        bug_id  = bug_id or finding.rule_id

        logger.info(
            "PatchGenerationEngine.generate() started — rule='%s', bug_id='%s'.",
            finding.rule_id,
            bug_id,
        )

        # ── 1. Input validation ──────────────────────────────────────────
        self._validate_inputs(finding, code)

        # ── 2. Classify bug category ─────────────────────────────────────
        repair_category = _classify_category(finding)
        logger.debug(
            "PatchGenerationEngine: classified as '%s'.", repair_category.value
        )

        # ── 3. Context selection ─────────────────────────────────────────
        context = self._context_selector.select(
            finding   = finding,
            code      = code,
            graph     = self._graph,
            evidence  = evidence,
            dfg_deps  = dfg_deps,
            cfg_graph = cfg_graph,
        )

        # ── 4. Edit planning ─────────────────────────────────────────────
        edit_plan = self._edit_planner.plan(
            finding         = finding,
            root_cause      = root_cause or _empty_root_cause(finding),
            repair_category = repair_category,
            graph           = self._graph,
            bug_id          = bug_id,
        )

        # ── 5. Style analysis ─────────────────────────────────────────────
        style_profile = self._syntax_preserver.analyze_style(code)
        style_summary = style_profile.to_prompt_summary()

        # ── 6. Prompt construction ────────────────────────────────────────
        system_prompt = self._prompt_builder.build_system_prompt()
        user_prompt   = self._prompt_builder.build_user_prompt(
            finding           = finding,
            root_cause        = root_cause or _empty_root_cause(finding),
            context           = context,
            edit_plan         = edit_plan,
            repair_category   = repair_category,
            style_summary     = style_summary,
            coding_guidelines = coding_guidelines,
        )

        # ── 7. LLM invocation with retries ───────────────────────────────
        raw_response, retry_count = await self._invoke_with_retry(
            system_prompt = system_prompt,
            user_prompt   = user_prompt,
        )

        t_llm = time.perf_counter()
        generation_ms = round((t_llm - t_start) * 1000, 2)

        # ── 8. Parse output ───────────────────────────────────────────────
        candidates = self._patch_parser.parse(raw_response, bug_id=bug_id)

        # ── 9. Generate unified diffs ─────────────────────────────────────
        self._diff_generator.generate_from_candidates(
            candidates,
            file_path  = finding.file_path,
            start_line = finding.line_number or 1,
        )

        # ── 10. Explain candidates ────────────────────────────────────────
        self._patch_explainer.explain_candidates(
            candidates      = candidates,
            finding         = finding,
            root_cause      = root_cause or _empty_root_cause(finding),
            repair_category = repair_category,
        )

        # ── 11. Validate style preservation ──────────────────────────────
        self._syntax_preserver.validate_preservation(candidates, style_profile)

        # ── 12. Assemble StructuredPatch ──────────────────────────────────
        rc_summary = (
            root_cause.primary.hypothesis
            if root_cause and root_cause.primary
            else finding.description
        )
        ev_summary = finding.evidence or ""

        provider_name = type(self._provider).__name__.replace("Provider", "").lower()
        gen_meta = GenerationMetadata(
            provider            = provider_name,
            prompt_version      = self._PROMPT_VERSION,
            generation_time_ms  = generation_ms,
            retry_count         = retry_count,
            context_chars       = context.estimated_char_count,
            candidates_requested= self._config.candidate_count,
            candidates_produced = len(candidates),
            reasoning_mode      = self._config.reasoning_mode.value,
            repair_style        = self._config.repair_style.value,
        )

        patch = self._patch_builder.build(
            finding             = finding,
            candidates          = candidates,
            edit_plan           = edit_plan,
            repair_category     = repair_category,
            generation_metadata = gen_meta,
            root_cause_summary  = rc_summary,
            evidence_summary    = ev_summary,
            bug_id              = bug_id,
        )

        # ── 13. Record history ────────────────────────────────────────────
        history_entry = PatchHistoryEntry(
            bug_id              = bug_id,
            rule_id             = finding.rule_id,
            repair_category     = repair_category,
            provider            = provider_name,
            prompt_version      = self._PROMPT_VERSION,
            patch_id            = patch.patch_id,
            candidate_count     = len(candidates),
            best_candidate_index= 0,
            generation_time_ms  = generation_ms,
        )
        self._history.record(history_entry)

        total_ms = round((time.perf_counter() - t_start) * 1000, 2)
        logger.info(
            "PatchGenerationEngine: patch '%s' complete — "
            "candidates=%d, retries=%d, context_chars=%d, total_ms=%.1f.",
            patch.patch_id,
            len(candidates),
            retry_count,
            context.estimated_char_count,
            total_ms,
        )
        return patch

    # ------------------------------------------------------------------
    # Retry logic
    # ------------------------------------------------------------------

    async def _invoke_with_retry(
        self,
        system_prompt: str,
        user_prompt:   str,
    ) -> tuple[str, int]:
        """
        Call the LLM provider with exponential backoff retries.

        Returns
        -------
        (raw_response, retry_count)
        """
        last_error: Optional[Exception] = None
        backoff    = self._config.retry_backoff

        from backend.core.ai.router import ModelRoutingEngine
        route = ModelRoutingEngine().route_task("complex_fix")

        for attempt in range(self._config.max_retries + 1):
            try:
                raw = await self._provider.generate_completion_async(
                    prompt          = user_prompt,
                    system_prompt   = system_prompt,
                    response_format = {"type": "json_object"},
                    model           = route.get("model"),
                    temperature     = self._config.temperature,
                    max_tokens      = self._config.max_tokens,
                )
                if raw:
                    return raw, attempt
                # Provider returned None/empty — treat as transient failure
                raise ProviderUnavailableError(
                    provider = type(self._provider).__name__,
                    details  = {"reason": "provider returned None/empty response"},
                )

            except (MalformedPatchOutputError, EmptyPatchError, ProviderUnavailableError) as exc:
                last_error = exc
                if attempt < self._config.max_retries:
                    wait = backoff ** attempt
                    logger.warning(
                        "PatchGenerationEngine: attempt %d/%d failed (%s: %s) — "
                        "retrying in %.1fs.",
                        attempt + 1,
                        self._config.max_retries + 1,
                        type(exc).__name__,
                        exc.message,
                        wait,
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.error(
                        "PatchGenerationEngine: all %d attempt(s) exhausted. "
                        "Last error: %s.",
                        self._config.max_retries + 1,
                        exc.message,
                    )

            except (ContextOverflowError, PromptOverflowError):
                # These are not transient — propagate immediately
                raise

            except Exception as exc:
                # Unexpected provider SDK errors
                last_error = ProviderUnavailableError(
                    provider = type(self._provider).__name__,
                    cause    = exc,
                    details  = {"original_error": str(exc)},
                )
                if attempt < self._config.max_retries:
                    wait = backoff ** attempt
                    logger.warning(
                        "PatchGenerationEngine: unexpected error on attempt %d/%d — "
                        "retrying in %.1fs: %s",
                        attempt + 1,
                        self._config.max_retries + 1,
                        wait,
                        exc,
                    )
                    await asyncio.sleep(wait)

        raise RetryExhaustedError(
            attempts = self._config.max_retries + 1,
            provider = type(self._provider).__name__,
        ) from last_error

    # ------------------------------------------------------------------
    # Input validation
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_inputs(finding: NormalizedFinding, code: str) -> None:
        """Raise ``MissingContextError`` for absent required inputs."""
        if not finding.line_text and not finding.evidence:
            raise MissingContextError(
                missing_field="line_text/evidence",
                details={"rule_id": finding.rule_id},
            )
        if not code or not code.strip():
            raise MissingContextError(
                missing_field="code",
                details={"rule_id": finding.rule_id},
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _empty_root_cause(finding: NormalizedFinding) -> RootCauseChain:
    """Return a minimal root cause chain for when none is provided."""
    from backend.core.analysis.root_cause import CausalNode, RootCauseChain as RC
    chain = RC(finding_rule_id=finding.rule_id)
    chain.nodes = [
        CausalNode(
            hypothesis       = finding.description,
            evidence_sources = [finding.rule_id],
            confidence       = finding.static_confidence,
        )
    ]
    return chain
