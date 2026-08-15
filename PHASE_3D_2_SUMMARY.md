# Phase 3D.2 — Autonomous Patch Validation Engine

> **Status:** COMPLETE — 132 tests pass (109 pre-existing + 19 new patch-validation + 4 groq-fallback). No patch regeneration, PR creation, or CI/CD execution has been added — those belong to Phase 3D.3.

---

## Overview

Phase 3D.2 adds a standalone **Autonomous Patch Validation Engine** that safely validates AI-generated patch candidates inside isolated workspaces. It integrates seamlessly after Phase 3D.1 patch generation and before engineering report delivery.

The complete pipeline:

```
StructuredPatch
      ↓
WorkspaceManager (isolated copy)
      ↓
PatchApplier (unified diff + block-replace fallback)
      ↓
SyntaxValidator (balanced braces, parens, preprocessor)
      ↓
BuildSystem + Compiler (CMake/Make/Ninja/Bazel/Direct)
      ↓
StaticValidator (AnalysisEngine before/after comparison)
      ↓
TestDiscovery + RegressionRunner (CTest/GTest/Catch2/scripts)
      ↓
QualityMetrics (scored across 5 dimensions)
      ↓
CandidateRanker (selects winner, explains rejections)
      ↓
ValidationReport (JSON, Markdown, SARIF)
      ↓
ValidationEngine returns ValidationReport
```

---

## New Subsystem: `backend/core/patch_validation/`

| Module | Responsibility |
|---|---|
| `exceptions.py` | Full exception hierarchy (9 typed exceptions) |
| `configuration.py` | `PatchValidationConfig` Pydantic config model |
| `validation_models.py` | All Pydantic schemas (Workspace, CompilationResult, RegressionResult, StaticReanalysisResult, ValidationMetrics, Diagnostics, CandidateRanking, ValidationReport) |
| `workspace_manager.py` | Context-managed isolated directory creation using tempfile; supports `temp_dir`, `git_worktree`, and `none` modes |
| `patch_applier.py` | Unified diff parser + block-replace fallback; supports hunk offset tolerance (±30 lines) |
| `syntax_validator.py` | C++ structure check: balanced `{}`, `()`, `[]`; preprocessor directive validation |
| `compiler.py` | `BaseCompiler` ABC + `GCCCompiler`, `ClangCompiler`, `MSVCCompiler` async runners |
| `compiler_registry.py` | Registry resolving compiler instances by string name |
| `build_system.py` | `CMakeBuildSystem`, `MakeBuildSystem`, `NinjaBuildSystem`, `BazelBuildSystem`, `NoneBuildSystem` + `BuildSystemRegistry` |
| `static_validator.py` | Re-runs `AnalysisEngine` on original vs patched file; identifies removed bug and new violations |
| `test_discovery.py` | Scans for CTest, shell scripts, and test binaries |
| `regression_runner.py` | Async subprocess test runner; parses CTest, GTest, Catch2, and binary exit codes |
| `quality_metrics.py` | Weighted scoring: bug_removed (0.4) + regression (0.3) + simplicity (0.1) − penalties |
| `diagnostics.py` | `DiagnosticsCollector` builder accumulating errors, warnings, timing, file refs, actions |
| `rollback.py` | In-memory file backup and restoration; guarantees no partial-state after failure |
| `candidate_ranker.py` | Multi-key sort and winner selection using configurable `min_acceptance_score` |
| `validation_report.py` | JSON, Markdown, and SARIF output formatters |
| `validator.py` | `CandidateValidator` — single-candidate pipeline orchestrator |
| `validation_engine.py` | `ValidationEngine` — top-level public API across all candidates; returns `ValidationReport` |
| `__init__.py` | Package exports for all public interfaces |

---

## Validation Scoring

| Dimension | Max Weight |
|---|---|
| Bug removal (static reanalysis) | +0.40 |
| Regression test pass | +0.30 |
| Patch simplicity (lines changed) | +0.10 |
| New bug penalty (per new finding) | −0.15 each |
| Warning increase penalty | −0.01 each |
| Compilation / syntax failure | → score = 0.0 |

Default acceptance threshold: **0.7**.

---

## Acceptance / Rejection Rules

**Auto-reject if:**
- Compilation fails
- Syntax check fails
- Patch apply fails (diff corrupt / file missing)
- Validation score < `min_acceptance_score`

**Auto-accept if:**
- Compilation succeeds
- Original bug removed (static reanalysis confirms)
- Regression tests pass (or none discovered)
- No critical new bugs introduced
- Score ≥ threshold

---

## Compiler Support

| Compiler | Class | Status |
|---|---|---|
| GCC / G++ | `GCCCompiler` | ✅ Implemented |
| Clang / Clang++ | `ClangCompiler` | ✅ Implemented |
| MSVC (cl.exe) | `MSVCCompiler` | ✅ Implemented |

## Build System Support

| Build System | Class | Status |
|---|---|---|
| CMake | `CMakeBuildSystem` | ✅ Implemented |
| Make | `MakeBuildSystem` | ✅ Implemented |
| Ninja (via CMake) | `NinjaBuildSystem` | ✅ Implemented |
| Bazel | `BazelBuildSystem` | ✅ Implemented |
| Direct (no system) | `NoneBuildSystem` | ✅ Implemented |

## Test Framework Support

| Framework | Discovery Method | Parse Method |
|---|---|---|
| CTest | `CTestTestfile.cmake` | Regex `tests passed / tests failed` |
| GoogleTest | Executable scan | `[  PASSED  ]` / `[  FAILED  ]` |
| Catch2 | Executable scan | `All tests passed` / `Failed N test cases` |
| Shell scripts | `run_tests.sh` / `.py` | Exit code |

---

## Test Coverage

| Test Class | Tests |
|---|---|
| `TestWorkspaceManager` | temp_dir isolation, in-place mode |
| `TestPatchApplier` | block replace, unified diff |
| `TestSyntaxValidator` | valid code, mismatched braces, malformed preprocessor |
| `TestCompilerAndRegistry` | GCC success, Clang warning parsing |
| `TestBuildSystem` | CMake configure |
| `TestStaticValidator` | bug removal detection |
| `TestRegression` | CTest discovery + runner output parsing |
| `TestMetricsCalculator` | scoring success and compile-fail cases |
| `TestCandidateRanker` | sort + winner selection |
| `TestReportGenerator` | JSON, Markdown, SARIF formatting |
| `TestRollbackManager` | file backup and restoration |
| `TestValidationEngineEndToEnd` | full pipeline with mocks |

**Total: 132 tests, 0 failures.**

---

## Files Created

### New Modules (19)
- `backend/core/patch_validation/__init__.py`
- `backend/core/patch_validation/exceptions.py`
- `backend/core/patch_validation/configuration.py`
- `backend/core/patch_validation/validation_models.py`
- `backend/core/patch_validation/workspace_manager.py`
- `backend/core/patch_validation/patch_applier.py`
- `backend/core/patch_validation/syntax_validator.py`
- `backend/core/patch_validation/compiler.py`
- `backend/core/patch_validation/compiler_registry.py`
- `backend/core/patch_validation/build_system.py`
- `backend/core/patch_validation/static_validator.py`
- `backend/core/patch_validation/test_discovery.py`
- `backend/core/patch_validation/regression_runner.py`
- `backend/core/patch_validation/quality_metrics.py`
- `backend/core/patch_validation/diagnostics.py`
- `backend/core/patch_validation/rollback.py`
- `backend/core/patch_validation/candidate_ranker.py`
- `backend/core/patch_validation/validation_report.py`
- `backend/core/patch_validation/validator.py`
- `backend/core/patch_validation/validation_engine.py`

### New Tests (1)
- `backend/tests/test_patch_validation.py`

---

## Deferred to Phase 3D.3

- Automatic patch regeneration loop
- Multi-agent repair with LLM feedback
- GitHub Pull Request creation
- Developer approval workflow
- Autonomous merge
- CI/CD pipeline execution
