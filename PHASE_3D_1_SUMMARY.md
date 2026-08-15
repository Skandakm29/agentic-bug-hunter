# Phase 3D.1 — Autonomous Patch Generation Engine
## Implementation Summary

> **Status:** COMPLETE — 109 tests pass (104 patch-generation + 5 static-engine). No compilation, execution, or patch-validation logic has been added — those belong to Phase 3D.2.

---

## What Was Built

Argus now contains a fully autonomous, provider-agnostic **Patch Generation Engine** that converts confirmed bug reports into structured, explainable, production-ready code patches.

The engine is a new Python package — `backend/core/patch_generation/` — integrated as an optional downstream stage in the existing `DetectionOrchestrator` pipeline.

---

## Package Architecture

```
backend/core/patch_generation/
├── __init__.py          Public API surface
├── exceptions.py        Custom exception hierarchy (9 classes)
├── patch_models.py      Pydantic domain models
├── repair_strategies.py Repair guidance registry (20 bug categories)
├── context_selector.py  Token-bounded context assembly
├── edit_planner.py      Structured edit-plan generation
├── prompt_builder.py    LLM prompt construction
├── patch_parser.py      JSON response parser + normaliser
├── diff_generator.py    Git-compatible unified diff generator
├── syntax_preserver.py  Style analysis + preservation validator
├── patch_explainer.py   Structured explanation enrichment
├── patch_history.py     Thread-safe in-memory audit store
├── patch_builder.py     StructuredPatch assembler
└── patch_generator.py   Top-level async engine (13-step pipeline)
```

---

## 13-Step Pipeline

```
validate_inputs
      ↓
classify_bug_category (keyword heuristic → RepairCategory)
      ↓
context_selector.select (token-bounded context window)
      ↓
edit_planner.plan (structured EditPlan + EditActions)
      ↓
syntax_preserver.analyze_style (detect indentation, braces, comments)
      ↓
prompt_builder.build (system + user prompts)
      ↓
LLM call with exponential-backoff retries
      ↓
patch_parser.parse (JSON → List[PatchCandidate])
      ↓
diff_generator.generate (unified diffs for each candidate)
      ↓
patch_explainer.explain (7-field structured explanations)
      ↓
syntax_preserver.validate (style preservation check)
      ↓
patch_builder.build → StructuredPatch
      ↓
patch_history.record (audit entry)
      ↓
return StructuredPatch
```

---

## Module Descriptions

### exceptions.py — Custom Exception Hierarchy

| Exception | Trigger |
|-----------|---------|
| `PatchGenerationError` | Base — all patch errors |
| `ProviderUnavailableError` | LLM provider SDK failure |
| `RetryExhaustedError` | All retry attempts consumed |
| `MalformedPatchOutputError` | Unparseable LLM JSON response |
| `EmptyPatchError` | Candidate original == patched |
| `ContextOverflowError` | Context exceeds token budget |
| `PromptOverflowError` | Prompt exceeds max_tokens |
| `UnsupportedBugTypeError` | No guidance for bug category |
| `MissingContextError` | Required input field absent |

### patch_models.py — Pydantic Domain Models

All models use `frozen=False` to allow enrichment during the pipeline (e.g. diff and explanation fields are populated in-place after LLM parsing).

Key models:

- `PatchGenerationConfig` — All engine parameters with validation
- `RepairCategory` (enum) — 20 bug categories + UNKNOWN
- `ContextWindow` — Token-bounded context bundle
- `EditPlan / EditAction` — Structured repair plan
- `PatchExplanation` — 7-field explanation model
- `PatchCandidate` — Single repair candidate with diff, explanation, style flags
- `FilePatch` — File-scoped candidate bundle
- `StructuredPatch` — Top-level output artifact with all candidates
- `PatchHistoryEntry` — Audit record

### repair_strategies.py — Repair Guidance Registry

- Covers all 20 `RepairCategory` values plus `UNKNOWN`
- Each `RepairGuidance` has: description, repair approach, common patterns, pitfalls, safety notes, example fix
- `registry.get(category)` always returns guidance, never raises
- Custom guidance can be registered at runtime via `registry.register(guidance)`

### context_selector.py — Token-Bounded Context Assembly

- Extracts a `±context_window` character window around the bug line
- Collects `#include` directives, RAG document snippets from `EvidenceGraph`, DFG dependencies, and cross-file call-site examples
- Raises `ContextOverflowError` when assembled context exceeds the configured budget

### edit_planner.py — Edit Plan Generation

- Produces a typed `EditPlan` with `EditAction` list
- Infers: cross-file impact, header update requirements, new helper functions, expected side effects
- Category-aware: e.g. `THREAD_SAFETY` adds synchronization side effects; `MEMORY_LEAK` sets `requires_new_helper=True`

### prompt_builder.py — LLM Prompt Construction

- `build_system_prompt()` → security-hardened Principal-Engineer-persona system prompt
- `build_user_prompt(...)` → all context, repair guidance, edit plan, style summary, strict JSON output schema
- Raises `PromptOverflowError` when `estimate_tokens(prompt) > max_tokens`
- Token estimation: `chars ÷ 4` (conservative approximation)

### patch_parser.py — JSON Response Parser

- Accepts output in three shapes: `{"candidates":[...]}`, `[...]`, or single-candidate object
- Strips markdown fences and leading prose before JSON extraction
- Filters candidates where `original_code == patched_code` → raises `EmptyPatchError`
- Clamps `confidence` to `[0.0, 1.0]`; assigns `preferred_rank` by descending confidence

### diff_generator.py — Unified Diff Generator

- Produces Git-compatible `--- a/file / +++ b/file` diffs via `difflib.unified_diff`
- Line-number offset: shifts `@@` hunk markers to file-level positions
- CRLF normalisation: diffs are always LF-only
- `generate_from_candidates()` enriches each `PatchCandidate.unified_diff` in-place
- `generate_summary_diff()` concatenates ranked candidates for human review

### syntax_preserver.py — Style Analysis & Validation

| Detection | Method |
|-----------|--------|
| Indentation | Count leading spaces vs tabs across ≥10 lines |
| Indent width | Statistical mode of per-line leading-space counts |
| Brace style | K&R (`{` same line) vs Allman (`{` next line) |
| Comment style | Line (`//`) vs block (`/*…*/`) dominance |
| Pointer style | `int *x` vs `int* x` |

Validation flags style violations on each candidate and sets `style_preserved = False`.

### patch_explainer.py — Explanation Enrichment

- If a candidate already has a complete `PatchExplanation` (from LLM), it is preserved unchanged
- Otherwise a heuristic fallback explanation is generated from finding, root cause, and repair guidance
- All 7 fields are guaranteed to be non-empty

### patch_history.py — Audit Store

Thread-safe in-memory store for all patch generation events. Designed for Phase 3D.2 swap-out to SQLite/Postgres via the same interface.

Key methods:
```python
store.record(entry)
store.get_by_bug_id("BUG-42")
store.get_by_category(RepairCategory.MEMORY_LEAK)
store.mark_accepted(entry_id, candidate_id)
exported = store.export()                  # → JSON bytes
store2   = PatchHistoryStore.import_from_json(exported)  # round-trip
store.stats()   # → {"total", "accepted_count", "by_category", ...}
```

### patch_builder.py — StructuredPatch Assembler

- Filters candidates below `min_candidate_confidence` (always keeps at least one as fallback)
- Re-ranks remaining candidates by confidence
- Populates `warnings` for: cross-file impact, API changes, style violations, low confidence, and category-specific risks
- Collects `dependencies` from edit plan actions

### patch_generator.py — Main Engine

- **Async-first**: `await engine.generate(finding, code, root_cause, evidence, bug_id=…)`
- **Retry**: Exponential backoff (`backoff^attempt` seconds) on `ProviderUnavailableError`, `MalformedPatchOutputError`, `EmptyPatchError`. Immediate raise on `ContextOverflowError`, `PromptOverflowError`
- **Category classifier**: keyword-based heuristic over `rule_id + description`
- **Provider-agnostic**: depends only on `BaseLLMProvider.generate_completion_async(...)`

---

## Integration

### DetectionOrchestrator

```python
# No patch generation (unchanged behaviour)
orch = DetectionOrchestrator()

# With patch generation (Phase 3D.1)
from backend.core.ai.providers.factory import LLMProviderFactory
from backend.core.patch_generation import PatchGenerationEngine

engine = PatchGenerationEngine(LLMProviderFactory.get_provider("groq"))
orch   = DetectionOrchestrator(patch_engine=engine)
result = orch.run(code=source, extension="cpp")

# result.structured_patches           → Dict[rule_id, StructuredPatch]
# result.finding_analyses[n].structured_patch  → StructuredPatch | None
```

Patch generation failures are caught and logged as warnings — they never abort the main analysis pipeline.

### Report Generator

| Format | Phase 3D.1 Additions |
|--------|----------------------|
| JSON | `findings[n].patch` — full `StructuredPatch.model_dump()` |
| SARIF | `results[n].fixes[n]` — best-candidate file changes with region + insertedContent |
| Markdown | `### 🔧 Autonomous Patch` subsection per finding — diff, explanation, warnings |
| HTML | Unchanged (patch detail in JSON/Markdown/SARIF) |

---

## Configuration Reference

| Parameter | Default | Description |
|-----------|---------|-------------|
| `candidate_count` | 3 | Number of repair candidates to request |
| `context_window` | 8000 | Character budget for context assembly |
| `max_tokens` | 8192 | LLM output token limit |
| `temperature` | 0.2 | Creativity vs determinism |
| `max_retries` | 3 | Retry attempts on transient failures |
| `retry_backoff` | 2.0 | Exponential backoff base (seconds) |
| `min_candidate_confidence` | 0.4 | Filter threshold for candidates |
| `repair_style` | `CONSERVATIVE` | How aggressive the repair should be |
| `reasoning_mode` | `STRUCTURED` | LLM reasoning strategy |
| `allow_new_helpers` | `True` | Permit new helper function proposals |

---

## Test Coverage

| Module | Tests |
|--------|-------|
| Exceptions | 10 |
| Patch Models | 7 |
| Repair Strategies | 7 |
| Context Selector | 7 |
| Edit Planner | 6 |
| Prompt Builder | 7 |
| Patch Parser | 12 |
| Diff Generator | 7 |
| Syntax Preserver | 9 |
| Patch Explainer | 3 |
| Patch History | 9 |
| Patch Builder | 6 |
| Engine Integration | 10 |
| Configuration | 4 |
| **TOTAL** | **104** |

All 104 + 5 pre-existing = **109 tests, 0 failures**.

---

## Design Principles Applied

| Principle | Implementation |
|-----------|---------------|
| Single Responsibility | Each of the 14 modules does exactly one thing |
| Open/Closed | Repair guidance extensible via `registry.register()` without modifying existing code |
| Dependency Inversion | Engine depends on `BaseLLMProvider` abstraction, not any concrete SDK |
| Fail-safe | Patch failures never abort the main analysis pipeline |
| Observability | Every generation event recorded in `PatchHistoryStore` with full metadata |
| Idempotency | Stateless modules; same input always produces structurally equivalent output |
| Thread Safety | `PatchHistoryStore` uses `threading.Lock`; all other sub-components are stateless |

---

## Deferred to Phase 3D.2

- Compilation / build verification of patches
- Regression test execution against patches
- Patch application to disk
- Persistent storage (SQLite/Postgres) for `PatchHistoryStore`
- Continual learning from accepted/rejected patches
- Multi-file patch application orchestration
- Patch conflict detection

---

## Files Created / Modified

### New Modules (14)
- `backend/core/patch_generation/__init__.py`
- `backend/core/patch_generation/exceptions.py`
- `backend/core/patch_generation/patch_models.py`
- `backend/core/patch_generation/repair_strategies.py`
- `backend/core/patch_generation/context_selector.py`
- `backend/core/patch_generation/edit_planner.py`
- `backend/core/patch_generation/prompt_builder.py`
- `backend/core/patch_generation/patch_parser.py`
- `backend/core/patch_generation/diff_generator.py`
- `backend/core/patch_generation/syntax_preserver.py`
- `backend/core/patch_generation/patch_explainer.py`
- `backend/core/patch_generation/patch_history.py`
- `backend/core/patch_generation/patch_builder.py`
- `backend/core/patch_generation/patch_generator.py`

### New Tests (1)
- `backend/tests/test_patch_generation.py` — 104 unit tests

### Modified (2)
- `backend/core/analysis/detection_orchestrator.py`
- `backend/core/analysis/report.py`
