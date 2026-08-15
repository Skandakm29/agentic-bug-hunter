# ARGUS — Enterprise AI Software Engineering Platform

ARGUS is an enterprise-grade software reasoning platform that combines deterministic static analysis, program graph construction, multi-agent LLM reasoning, evidence-backed confidence scoring, autonomous patch generation, and patch validation to detect, localize, remediate, and verify defects in C++ codebases — with full explainability and auditability at every step.

---

## Architecture Overview

```
Repository
      |
      v
Parsing Layer            AST / UIR / CFG / DFG / IPA
      |
      v
Repository Understanding  Knowledge Graph, Symbol Table, Dependency Graph
      |
      v
Rule Engine               Deterministic, pluggable, language-agnostic
      |
      v
Multi-Agent Reasoning     Validator, Fixer, ReportGenerator agents
      |
      v
Evidence Graph            Typed nodes and directed edges per finding
      |
      v
5-Stage Validation        Evidence -> Semantic -> Architecture -> Consistency -> Confidence
      |
      v
Suppression Pipeline      5 composable false-positive filters
      |
      v
Root Cause Analysis       DFG backward slice + IPA causal chains
      |
      v
Patch Generation          Ranked repair candidates with LLM reasoning (Phase 3D.1)
      |
      v
Patch Validation          Isolated workspace verification, compilation, regression (Phase 3D.2)
      |
      v
Repair Loop (Multi-Agent) Closed-loop orchestrator, Validator/Planner/Reviewer agents (Phase 3D.3)
      |
      v
Engineering Reports       JSON / SARIF 2.1.0 / Markdown / HTML / RepairReport
```

---

## Key Capabilities

### Phase 1 — Foundation
- Monaco Editor-based UI with syntax highlighting and real-time diagnostics
- Hybrid verification combining fast static rules with async LLM semantic validation
- Core analysis exposed as Model Context Protocol (MCP) tools for IDE AI agents
- Production Dockerfile and docker-compose for zero-config local deployment

### Phase 2 — Static Analysis Platform
- Pluggable rule engine: rules inherit `BaseRule`, register via `RuleRegistry`, execute over a typed `CodeRepresentation` IR
- Language-agnostic parser registry mapping file extensions to parser implementations
- `MCPCoordinator` wrapping tool execution in `asyncio.wait_for` timeout pools with structured error handling
- Normalized finding schema: all findings emit `NormalizedFinding` (Pydantic) with rule_id, severity, confidence, evidence, and remediation

### Phase 3A — Intelligent Analysis Infrastructure
- Unified Intermediate Representation (UIR): language-agnostic statement nodes (Assignment, Branch, Call, Return, FunctionDecl)
- Symbol table and scope resolution: `ScopedContext` with parent-child namespace walking
- Type inference engine: literal constant typing and prefix-based return type inference
- Control Flow Graph (CFG): basic block construction with branch-edge mapping
- Data Flow Graph (DFG): variable propagation chains via assignment traversal
- Interprocedural Call Tracer (IPA): caller-to-callee call graph with multi-function scope tracking

### Phase 3B.1 — Multi-Agent Runtime
- DAG Execution Scheduler: `ExecutionGraph` with topological sort, cycle detection, and concurrent node execution via `asyncio.gather`
- Agent Message Bus: `AgentMessageBus` with typed `MessageEnvelope` contracts, correlation IDs, and async handler routing
- State Manager: checkpoint persistence for replay and disaster recovery

### Phase 3B.2 — Enterprise Knowledge Systems
- Working Memory: LRU-evicting in-process cache for active analysis context
- Semantic Memory: long-term vector-similarity knowledge store for confirmed bug patterns and false-positive suppression
- Repository Knowledge Graph: `RepositoryKnowledgeGraph` with symbol registry and call-graph edge traversal
- Model Router: complexity-aware LLM model selection across classification, validation, and repair task types

### Phase 3C.1 — Code Intelligence Engine
- Full statement-level IR with typed AST primitives
- Circular-shadow-bug prevention via parent-scope walking
- CFG, DFG, and IPA program graph construction suite, all verified against regression suite

### Phase 3C.2 — Intelligent Bug Detection and Root Cause Reasoning
- Evidence Graph: every finding materialized as a directed typed graph (12 node types, 9 edge types), fully JSON-serializable
- Confidence Engine: reproducible weighted formula — `0.45 x static + 0.25 x semantic + 0.20 x consensus + 0.10 x history - penalties`
- Explainability Engine: three output formats (Markdown, one-paragraph summary, JSON forensic dict) answering 7 mandatory explainability questions
- Suppression Pipeline: 5 composable filters in cheapest-first order with full audit log and extensible `BaseSuppressionFilter`
- Multi-Hop Reasoner: BFS/DFS graph traversal with confidence accumulation, cycle detection, and depth cap
- Bug Localizer: three-stage fallback (statement -> function/class -> file); ranked `CodeSpan` output
- Cross-File Reasoner: unified file-dependency and call-graph traversal with reverse-dependency lookup
- Root Cause Analyzer: backward DFG slice and IPA caller traversal producing ranked `RootCauseChain` with eliminated hypotheses preserved
- Patch Planner: 3 ranked `RepairStrategy` objects; score = `correctness x 0.5 + maintainability x 0.3 - risk x 0.2`
- Remediation Reasoner: strategy evaluation against SemanticMemory and architecture rules; `NO_VIABLE_STRATEGY` guard
- Regression Analyzer: forward IPA traversal; per-component `regression_risk = reach x centrality x (1 - coverage)`; API, security, and performance flags
- Validation Pipeline: 5-stage gate — Stage 1 hard-rejects, Stages 2-4 degrade severity to LOW, Stage 5 delegates to suppression
- Report Generator: JSON, SARIF 2.1.0, Markdown, dark-theme self-contained HTML; streaming support for >1K findings
- Detection Orchestrator: single `run()` facade combining all 11 subsystems; returns four pre-rendered report formats

### Phase 3D.1 — Autonomous Patch Generation
- End-to-end `PatchGenerationEngine` producing `StructuredPatch` artifacts from confirmed findings
- Context Selector: token-bounded repository context assembly from CFG, DFG, evidence, and call graph
- Edit Planner: pre-generation scope analysis producing structured `EditPlan` with per-file `EditAction` objects
- Prompt Builder: versioned system and user prompt assembly with repair category guidance injection
- Patch Output Parser: robust JSON candidate extraction with malformed-output recovery
- Unified Diff Generator: git-compatible diff generation per candidate
- Patch Explainer: rule-based fallback explanation synthesizer requiring no additional LLM calls
- Syntax Preserver: style detection and candidate-level style violation reporting
- Patch Builder: confidence-based candidate filtering, ranking, and `StructuredPatch` assembly
- Patch History Store: thread-safe, in-memory generation event log with serialization support
- 20 supported repair categories across memory, control flow, API usage, lifetime, concurrency, and type safety
- LLM Fallback Router: sequential model fallback across configured models on provider failure (added in 3D.1 reliability pass)
- 104 unit tests, 0 failures

### Phase 3D.2 — Autonomous Patch Validation
- `ValidationEngine`: top-level orchestrator running the full validation pipeline across all candidates and returning a `ValidationReport`
- `WorkspaceManager`: context-managed isolated directory creation using `tempfile`; supports `temp_dir`, `git_worktree`, and `none` modes; the original repository is never modified except in `none` mode, which validates in place by design. Note that `git_worktree` currently falls back to the same copy-based isolation as `temp_dir` rather than creating a real worktree
- `PatchApplier`: unified diff parser with hunk offset tolerance (±30 lines) and block-replace fallback for partial-match diffs
- `SyntaxValidator`: pre-compilation structural check for balanced braces, parentheses, brackets, and preprocessor directive syntax
- Compiler abstraction: `BaseCompiler` ABC with `GCCCompiler`, `ClangCompiler`, and `MSVCCompiler` async subprocess runners capturing stdout, stderr, warnings, errors, and timing
- `CompilerRegistry`: string-based compiler resolver with extensible registration
- Build system abstraction: `CMakeBuildSystem`, `MakeBuildSystem`, `NinjaBuildSystem`, `BazelBuildSystem`, `NoneBuildSystem`, and `BuildSystemRegistry`
- `StaticValidator`: re-runs `AnalysisEngine` on the patched file and compares findings to confirm bug removal and detect newly introduced violations. Bug removal requires two independent signals — the flagged statement's signature must be gone *and* the per-rule occurrence count must drop — so neither a cosmetic edit nor an unrelated same-rule hit nearby can be mistaken for a fix
- `TestDiscovery`: scans for CTest configurations, shell/Python test scripts, and test binaries (GoogleTest, Catch2)
- `RegressionRunner`: async subprocess test runner with output parsers for CTest, GoogleTest, Catch2, and binary exit codes
- `QualityMetrics`: weighted scoring across bug removal (0.4), regression pass (0.3), simplicity (0.1), minus penalties for new bugs and warning increases
- `DiagnosticsCollector`: step-by-step accumulator for errors, warnings, timing records, affected files, and remediation actions
- `RollbackManager`: in-memory file backup and restore guaranteeing no partial state on failure
- `CandidateRanker`: multi-key sort and winner selection with configurable minimum acceptance score
- `ValidationReportGenerator`: JSON, Markdown, and SARIF 2.1.0 output formatters
- 19 unit tests added, 132 total tests, 0 failures

- Pluggable Repair Policies: dynamic configuration presets (`default`, `conservative`, `aggressive`) resolved via `RepairPolicyRegistry`

### Phase 3D.3 — Autonomous Multi-Agent Repair Loop
- `RepairOrchestrator`: public entry point wrapping the closed-loop iterative pipeline
- `RepairLoop`: controls execution flow, executing generate/refine -> validate -> score -> feedback -> reason -> plan loop
- Cooperative Agent Subsystem: 5 specialized agents (Validator, Patch Generator, Reviewer, Planner, Report) coordinated via `AgentManager`
- `FeedbackEngine`: maps compile/regression diagnostic failure patterns to structured hints
- `ReasoningEngine`: low-temperature LLM-backed failure advisor providing element-level preserve/modify instructions
- `RefinementEngine`: targeted patch optimizer with 8 code-adaptation strategies (null guards, Smart Pointers, boundary bounds checking)
- `PlanningEngine`: prioritizes strategies, determining whether to refine the best candidate, switch APIs, or escalate
- `RepairScorer`: weighted composite formula (`validation x 0.40 + confidence x 0.20 + (1-risk) x 0.15 + maintainability x 0.10 + simplicity x 0.10 + static x 0.05`)
- `ConvergenceDetector`: absolute/relative delta and window variance score plateau checking
- `TerminationPolicy`: evaluates 7 stop conditions (Accepted winner, iteration limit, timeout, repeated failures, pool exhaustion, convergence, manual stop)
- State & Provenance suite: thread-safe `CandidatePool` tracking lineages, `RepairMemory` sliding window, and append-only replayable `AuditTrail`
- ~120 new unit/integration tests added (252 total tests), 0 failures

---

## Repository Structure

```
argus/
├── backend/
│   ├── core/
│   │   ├── analysis/                     # Phases 3A-3C analysis engines
│   │   │   ├── parsers/                  # Language frontend and UIR
│   │   │   │   ├── base.py               # CodeRepresentation IR contract
│   │   │   │   ├── registry.py           # ParserRegistry
│   │   │   │   ├── cpp.py                # C++ line scanner and IR builder
│   │   │   │   └── uir.py                # Unified Intermediate Representation nodes
│   │   │   ├── rules/                    # Pluggable rule engine
│   │   │   │   ├── base.py               # BaseRule abstract class
│   │   │   │   ├── registry.py           # RuleRegistry
│   │   │   │   └── rdi_rules.py          # RDI API check rules
│   │   │   ├── schemas.py                # NormalizedFinding + AnalysisReport (Pydantic)
│   │   │   ├── engine.py                 # AnalysisEngine — full Phase 3C.2 pipeline
│   │   │   ├── detection_orchestrator.py # DetectionOrchestrator — end-to-end facade
│   │   │   ├── repository_scanner.py     # RepositoryScanner — whole-repo traversal
│   │   │   ├── graph.py                  # DAG ExecutionGraph scheduler
│   │   │   ├── state.py                  # StateManager — checkpoint persistence
│   │   │   ├── repo_graph.py             # RepositoryKnowledgeGraph
│   │   │   ├── symbols.py                # ScopedContext + ScopedSymbol
│   │   │   ├── types.py                  # TypeInferencer
│   │   │   ├── cfg.py                    # CFGGenerator — basic blocks and branch edges
│   │   │   ├── dfg.py                    # DFGConstructor — variable propagation chains
│   │   │   ├── ipa.py                    # IPATracer — interprocedural call graph
│   │   │   ├── evidence.py               # EvidenceGraph + EvidenceGraphBuilder
│   │   │   ├── confidence.py             # ConfidenceEngine — weighted reproducible scoring
│   │   │   ├── explain.py                # ExplainabilityEngine
│   │   │   ├── suppression.py            # SuppressionPipeline — 5 composable filters
│   │   │   ├── multi_hop.py              # MultiHopReasoner — BFS/DFS graph traversal
│   │   │   ├── localizer.py              # BugLocalizer — statement/function/file spans
│   │   │   ├── cross_file.py             # CrossFileReasoner — cross-module dependency tracing
│   │   │   ├── root_cause.py             # RootCauseAnalyzer — causal chain inference
│   │   │   ├── patch.py                  # PatchPlanningEngine — ranked repair strategies
│   │   │   ├── remediation.py            # RemediationReasoner — strategy evaluation
│   │   │   ├── regression.py             # RegressionAnalyzer — forward impact analysis
│   │   │   ├── validation_pipeline.py    # ValidationPipeline — 5-stage finding gate
│   │   │   └── report.py                 # ReportGenerator — JSON/SARIF/Markdown/HTML
│   │   ├── ai/                           # LLM reasoning layer
│   │   │   ├── agents/
│   │   │   │   ├── base.py               # BaseAgent contract
│   │   │   │   ├── validator.py          # ValidatorAgent — semantic false-positive check
│   │   │   │   ├── fixer.py              # FixerAgent — correction generation
│   │   │   │   └── report_generator.py   # ReportGeneratorAgent — audit compilation
│   │   │   ├── memory/
│   │   │   │   ├── working.py            # WorkingMemory — LRU in-process cache
│   │   │   │   └── semantic.py           # SemanticMemory — vector similarity store
│   │   │   ├── providers/
│   │   │   │   ├── base.py               # BaseLLMProvider abstract interface
│   │   │   │   ├── groq.py               # GroqProvider with sequential model fallback
│   │   │   │   └── factory.py            # LLMProviderFactory
│   │   │   ├── bus.py                    # AgentMessageBus + MessageEnvelope
│   │   │   └── router.py                 # ModelRoutingEngine — task-aware model routing
│   │   ├── patch_generation/             # Phase 3D.1 — Autonomous Patch Generation
│   │   │   ├── patch_generator.py        # PatchGenerationEngine — main entry point
│   │   │   ├── patch_models.py           # All generation Pydantic models
│   │   │   ├── patch_builder.py          # StructuredPatch assembly
│   │   │   ├── patch_parser.py           # LLM output parser with recovery
│   │   │   ├── patch_explainer.py        # Rule-based explanation synthesizer
│   │   │   ├── patch_history.py          # PatchHistoryStore
│   │   │   ├── context_selector.py       # Token-bounded context assembly
│   │   │   ├── edit_planner.py           # EditPlan + EditAction construction
│   │   │   ├── prompt_builder.py         # Versioned system and user prompt builder
│   │   │   ├── diff_generator.py         # Unified diff generation
│   │   │   ├── syntax_preserver.py       # Style detection and violation reporting
│   │   │   ├── repair_strategies.py      # RepairGuidanceRegistry (20 categories)
│   │   │   ├── exceptions.py             # Generation exception hierarchy
│   │   │   └── __init__.py               # Package exports
│   │   ├── patch_validation/             # Phase 3D.2 — Autonomous Patch Validation
│   │   │   ├── validation_engine.py      # ValidationEngine — top-level public API
│   │   │   ├── validator.py              # CandidateValidator — single-candidate pipeline
│   │   │   ├── workspace_manager.py      # Isolated workspace creation and cleanup
│   │   │   ├── patch_applier.py          # Unified diff and block-replace applier
│   │   │   ├── syntax_validator.py       # Pre-compilation structural syntax check
│   │   │   ├── compiler.py               # GCC, Clang, MSVC async runners
│   │   │   ├── compiler_registry.py      # Compiler resolver registry
│   │   │   ├── build_system.py           # CMake/Make/Ninja/Bazel/Direct + registry
│   │   │   ├── static_validator.py       # Before/after static analysis comparison
│   │   │   ├── test_discovery.py         # CTest, script, and binary test discovery
│   │   │   ├── regression_runner.py      # Async test runner with output parsers
│   │   │   ├── quality_metrics.py        # Weighted validation score computation
│   │   │   ├── candidate_ranker.py       # Multi-key candidate sort and winner selection
│   │   │   ├── diagnostics.py            # DiagnosticsCollector builder
│   │   │   ├── rollback.py               # File backup and atomic restore
│   │   │   ├── validation_report.py      # JSON, Markdown, and SARIF report formatters
│   │   │   ├── validation_models.py      # All validation Pydantic models
│   │   │   ├── configuration.py          # PatchValidationConfig
│   │   │   ├── exceptions.py             # Validation exception hierarchy
│   │   │   └── __init__.py               # Package exports
│   │   ├── autonomous_repair/            # Phase 3D.3 — Autonomous Multi-Agent Repair Loop
│   │   │   ├── orchestrator.py           # RepairOrchestrator public facade entry point
│   │   │   ├── repair_loop.py            # RepairLoop core pipeline controller
│   │   │   ├── configuration.py          # RepairConfiguration Pydantic model
│   │   │   ├── exceptions.py             # Loop specific exceptions
│   │   │   ├── repair_models.py          # Pydantic models for loop, agent decisions, feedback
│   │   │   ├── candidate_pool.py         # Thread-safe CandidatePool with lineage tracking
│   │   │   ├── memory.py                 # RepairMemory context manager
│   │   │   ├── audit.py                  # AuditTrail log export and replay
│   │   │   ├── metrics.py                # RepairMetricsCollector session timelines
│   │   │   ├── scoring.py                # RepairScorer composite score engine
│   │   │   ├── convergence.py            # ConvergenceDetector plateau detector
│   │   │   ├── termination.py            # TerminationPolicy stop conditions
│   │   │   ├── feedback_engine.py        # FeedbackEngine raw log to action mapper
│   │   │   ├── reasoning_engine.py       # LLM ReasoningEngine failure analyst
│   │   │   ├── refinement_engine.py      # RefinementEngine minimal patch adapter
│   │   │   ├── planning.py               # PlanningEngine strategy selector
│   │   │   ├── policy.py                 # RepairPolicy strategy registry
│   │   │   ├── agent_manager.py          # AgentManager cache and registration
│   │   │   ├── agents/                   # Specialized AI cooperative agents
│   │   │   │   ├── __init__.py
│   │   │   │   ├── base_agent.py         # BaseRepairAgent contract
│   │   │   │   ├── validator_agent.py    # ValidatorAgent failure explainer
│   │   │   │   ├── patch_generator_agent.py # PatchGeneratorAgent (3D.1 wrapper)
│   │   │   │   ├── reviewer_agent.py     # ReviewerAgent maintainability rater
│   │   │   │   ├── planner_agent.py      # PlannerAgent loop strategy advisor
│   │   │   │   └── report_agent.py       # ReportAgent engineering report writer
│   │   │   └── __init__.py
│   │   ├── mcp/                          # Model Context Protocol layer
│   │   │   ├── registry.py               # MCPToolRegistry
│   │   │   ├── coordinator.py            # MCPCoordinator
│   │   │   └── tools.py                  # MCP tool implementations
│   │   ├── config.py                     # Settings loader
│   │   └── orchestrator.py              # Legacy pipeline orchestrator
│   ├── api/
│   │   ├── router.py                     # Core HTTP routes
│   │   └── repository.py                 # Repository scan / upload / export routes
│   ├── tests/
│   │   ├── test_static_engine.py         # Static engine regression suite
│   │   ├── test_analysis_pipeline.py     # Phases 3A / 3C.2 pipeline and graph builders
│   │   ├── test_patch_generation.py      # Phase 3D.1 unit tests
│   │   ├── test_patch_validation.py      # Phase 3D.2 unit tests
│   │   ├── test_autonomous_repair.py     # Phase 3D.3 multi-agent loop tests
│   │   ├── test_api_contract.py          # Cross-subsystem seams and model routing
│   │   ├── test_integration_e2e.py       # detect → generate → validate, real toolchain
│   │   └── test_groq_provider.py         # LLM fallback unit tests
│   ├── main.py                           # FastAPI application entry point
│   ├── mcp_server.py                     # FastMCP server CLI entry point
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/                             # Next.js UI
│   ├── src/
│   │   ├── app/
│   │   ├── components/                   # Monaco Editor, Header, Results Panel
│   │   └── lib/                          # API fetch client
│   ├── package.json
│   └── Dockerfile
├── docs/
│   ├── architecture.md
│   ├── setup.md
│   ├── deployment.md
│   ├── troubleshooting.md
│   ├── contributing.md
│   └── phase_3d3/                        # Phase 3D.3 multi-agent docs
├── PHASE_1_SUMMARY.md
├── PHASE_3A_SUMMARY.md
├── PHASE_3B_1_SUMMARY.md
├── PHASE_3B_2_SUMMARY.md
├── PHASE_3C_1_SUMMARY.md
├── PHASE_3C_2_SUMMARY.md
├── PHASE_3D_1_SUMMARY.md
├── PHASE_3D_2_SUMMARY.md
├── PHASE_3D_3_SUMMARY.md
└── docker-compose.yml
```

---

## Quick Start

### Prerequisites
- Python 3.12+
- Node.js 20+
- A [Groq API key](https://console.groq.com) (free tier is sufficient)

### Backend

```bash
cd backend
python -m venv venv

# Windows
.\\venv\\Scripts\\activate
# macOS / Linux
source venv/bin/activate

pip install -r requirements.txt
export GROQ_API_KEY="your_api_key_here"   # Windows: $env:GROQ_API_KEY="..."
python main.py
```

API available at `http://localhost:8000`

#### Model configuration

Groq retires hosted models on a rolling basis, and a decommissioned ID fails
every request in the fallback chain — which silently disables every LLM
feature. All model IDs are therefore environment-overridable, so a retirement
can be worked around without a code change:

| Variable | Default | Used for |
|---|---|---|
| `GROQ_MODEL` | `llama-3.1-8b-instant` | Classification, static-finding validation |
| `GROQ_REASONING_MODEL` | `llama-3.3-70b-versatile` | Patch generation, refinement, failure reasoning |
| `GROQ_FALLBACK_MODELS` | `llama-3.3-70b-versatile,llama-3.1-8b-instant` | Comma-separated chain tried on failure |

Check [console.groq.com/docs/models](https://console.groq.com/docs/models) for
the current roster. `ModelRoutingEngine` reads these settings, so there is a
single place to update.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

UI available at `http://localhost:3000`

### Docker (full stack)

```bash
export GROQ_API_KEY="your_api_key_here"
docker compose up --build
```

---

## Running Tests

```bash
cd backend
python -m unittest discover -s tests
```

Expected output:

```
Ran 272 tests in ~5s

OK
```

Test breakdown:

| Suite | Tests | Covers |
|---|---|---|
| `test_static_engine.py` | 5 | Rule engine behaviour |
| `test_analysis_pipeline.py` | 32 | Phases 3A / 3C.2 — program graphs, root cause, remediation, `DetectionOrchestrator` |
| `test_patch_generation.py` | 105 | Phase 3D.1 |
| `test_patch_validation.py` | 26 | Phase 3D.2 |
| `test_autonomous_repair.py` | 34 | Phase 3D.3 multi-agent loop |
| `test_api_contract.py` | 16 | Cross-subsystem seams, model routing, documented extension points |
| `test_integration_e2e.py` | 16 | detect → generate → validate → repair loop, against a real file |
| `test_repository_scanner.py` | 34 | Repo traversal, graph indexes, `/repository/*` routes |
| `test_groq_provider.py` | 4 | LLM fallback chain |
| **Total** | **272** | |

`test_integration_e2e.py` compiles with a real toolchain; the compilation
cases skip automatically when `g++` is not on `PATH`.

---

## Usage

### End-to-end analysis (Phase 3C.2 API)

```python
from backend.core.analysis.detection_orchestrator import DetectionOrchestrator

orch   = DetectionOrchestrator()
result = orch.run(
    code      = "void IRAM_ATTR my_isr() { delay(100); }",
    extension = "cpp",
    file_path = "src/driver.cpp",   # optional, but enables localization,
)                                   # cross-file tracing, regression impact,
                                    # and SARIF physical locations

for ef in result.enriched_findings:
    print(ef.finding.rule_id, ef.finding.severity)
    print(ef.confidence_result.final_score)
    print(ef.explanation.summary)

for fa in result.finding_analyses:
    print("Root cause:", fa.root_cause.primary.hypothesis)
    print("Best fix:  ", fa.patch_plan.best.description)

with open("report.sarif", "wb") as f:
    f.write(result.sarif_report)
```

### Patch generation (Phase 3D.1 API)

```python
from backend.core.ai.providers.factory import LLMProviderFactory
from backend.core.patch_generation import PatchGenerationEngine, PatchGenerationConfig

provider = LLMProviderFactory.get_provider("groq")
engine   = PatchGenerationEngine(provider, PatchGenerationConfig())

patch = await engine.generate(
    finding    = finding,      # NormalizedFinding
    code       = source_code,
    root_cause = root_cause,   # RootCauseChain
    evidence   = evidence,     # EvidenceGraph
    bug_id     = "BUG-001",
)
# patch.file_patches[0].candidates  -> List[PatchCandidate]
```

### Patch validation (Phase 3D.2 API)

```python
from backend.core.patch_validation import ValidationEngine, PatchValidationConfig

config = PatchValidationConfig(
    compiler_type   = "gcc",
    build_system    = "cmake",
    regression_enabled       = True,
    static_analysis_enabled  = True,
    min_acceptance_score     = 0.7,
)
engine = ValidationEngine(config)

report = await engine.validate_patch(
    patch              = patch,           # StructuredPatch from 3D.1
    original_code_path = "/path/to/repo",
)

if report.accepted:
    print("Winner:", report.winner_candidate_id)
    print("Score: ", report.metrics[report.winner_candidate_id].score)
else:
    print("No candidate passed validation thresholds.")
    for error in report.diagnostics.errors:
        print(" -", error)
```

### Validation report formats

```python
from backend.core.patch_validation import ValidationReportGenerator

markdown = ValidationReportGenerator.to_markdown(report)
json_str = ValidationReportGenerator.to_json(report)
sarif    = ValidationReportGenerator.to_sarif(report)
```

### Autonomous multi-agent repair loop (Phase 3D.3 API)

```python
from backend.core.ai.providers.factory import LLMProviderFactory
from backend.core.autonomous_repair import RepairOrchestrator, RepairConfiguration
from backend.core.patch_validation import PatchValidationConfig

provider = LLMProviderFactory.get_provider("groq")
config   = RepairConfiguration(max_iterations=5, acceptance_threshold=0.75)

# Toolchain settings for the validation step. Defaults are cmake + gcc with
# regression testing on; override them to match the target project, or the
# build will fail and no candidate can clear the acceptance threshold.
validation_config = PatchValidationConfig(
    compiler_type           = "gcc",
    build_system            = "none",   # compile sources directly
    regression_enabled      = False,
    static_analysis_enabled = True,
)

orchestrator = RepairOrchestrator(provider, config, validation_config)

session = await orchestrator.run(
    finding            = finding,      # NormalizedFinding
    code               = source_code,
    root_cause         = root_cause,   # RootCauseChain
    evidence           = evidence,     # EvidenceGraph
    original_code_path = "/path/to/repo",
    bug_id             = "BUG-001",
)

if session.accepted:
    print("Repair accepted with score:", session.best_composite_score)
    print("Report summary:", session.report.executive_summary)
else:
    print("Termination reason:", session.termination_reason.value)
```

### Backward-compatible static analysis API

```python
from backend.core.analysis.engine import AnalysisEngine

findings = AnalysisEngine().analyze_plain(code)   # List[NormalizedFinding]
```

---

## REST API

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/analyze` | Single-snippet analysis — body: `{"code": "..."}` |
| `GET` | `/rules` | List all active rules with metadata |
| `GET` | `/health` | Server availability check |
| `GET` | `/ollama/status` | LLM provider connectivity check |
| `POST` | `/repository/scan` | Analyse a folder on this machine — body: `{"path": "..."}` |
| `POST` | `/repository/upload` | Analyse an uploaded `.zip` (multipart `file`) |
| `GET` | `/repository/scans` | List scans held in memory |
| `GET` | `/repository/scan/{id}` | Re-fetch a completed scan |
| `GET` | `/repository/scan/{id}/file?path=` | Source of one scanned file |
| `GET` | `/repository/scan/{id}/export/{fmt}` | Download `json` \| `sarif` \| `markdown` \| `html` |

Repository scanning runs the deterministic static pipeline only — **no API key
is required** and no network calls are made. Traversal skips `.git`, `build`,
`vendor`, `node_modules`, and similar directories, and is bounded by
`max_files` (default 2000) and a 1 MB per-file cap.

---

## Using the UI

With both servers running, open `http://localhost:3000`.

**Repository scan** (default tab)
1. Paste an absolute folder path — e.g. `C:\projects\firmware` — and press
   **Scan folder**. Or click **Upload .zip** to analyse an archive; a
   GitHub-style zip with a single top-level folder is unwrapped automatically.
2. The summary strip shows files scanned, findings, files affected, suppressed
   count, duration, and a severity breakdown.
3. The left pane groups findings by file. Filter by text or click a severity
   chip to narrow the list.
4. Select a finding to see its confidence decomposition, the flagged line in
   source context, the evidence, the root-cause chain with any alternative
   hypotheses, the ranked repair strategies with scores, and the remediation.
5. Download the whole scan as JSON, SARIF 2.1.0, Markdown, or HTML.

**Snippet analyzer** (second tab) is the original Monaco editor flow. Unlike
repository scanning it calls the LLM agents, so it needs `GROQ_API_KEY`.

Throughput is roughly 5 ms per file on a warm filesystem cache — a 265-file C
codebase scans in about 1.3 seconds.

---

## Extending the Platform

### Add a detection rule

```python
from backend.core.analysis.rules.base import BaseRule
from backend.core.analysis.rules.registry import RuleRegistry

@RuleRegistry.register
class MyRule(BaseRule):
    rule_id    = "my_custom_rule"
    language   = "cpp"
    severity   = "HIGH"
    confidence = 0.85

    def execute(self, representation): ...
```

### Add a language parser

```python
from backend.core.analysis.parsers.base import BaseParser
from backend.core.analysis.parsers.registry import ParserRegistry

class PythonParser(BaseParser):
    def parse(self, code: str) -> CodeRepresentation: ...

ParserRegistry.register("py", PythonParser)
```

### Add a suppression filter

```python
from backend.core.analysis.suppression import BaseSuppressionFilter, SuppressionPipeline

class TeamFilter(BaseSuppressionFilter):
    name = "TeamFilter"
    def apply(self, findings): ...

pipeline = SuppressionPipeline.default()
pipeline.register_filter(TeamFilter())
```

### Add a compiler backend

```python
from backend.core.patch_validation.compiler import BaseCompiler
from backend.core.patch_validation.compiler_registry import CompilerRegistry
from backend.core.patch_validation.validation_models import CompilationResult

class IntelCompiler(BaseCompiler):
    async def compile(self, workspace_path, source_files, build_command=None, timeout=30) -> CompilationResult:
        ...

CompilerRegistry.register("intel", IntelCompiler)
```

### Add a build system backend

```python
from backend.core.patch_validation.build_system import BaseBuildSystem, BuildSystemRegistry

class MesonBuildSystem(BaseBuildSystem):
    async def configure(self, workspace_path, compiler_type, parallel_jobs=4) -> bool: ...
    async def build(self, workspace_path, compiler_type, parallel_jobs=4, timeout=60): ...

BuildSystemRegistry.register("meson", MesonBuildSystem)
```

---

## Performance Targets

| Operation | Target |
|---|---|
| Single-file static analysis | < 100 ms |
| Full 3C.2 pipeline per file | < 5 s |
| Root cause analysis | < 200 ms |
| Regression impact analysis | < 500 ms |
| Report generation (1K findings) | < 1 s |
| Patch generation per finding | < 15 s |
| Patch validation per candidate | < 60 s (compilation + tests) |

---

## Phase Completion Status

| Phase | Status | Description |
|---|---|---|
| Phase 1 | Complete | Foundation — editor, hybrid analysis, MCP, Docker |
| Phase 2A | Complete | Static analysis platform — rule engine, IR, parsers, MCP |
| Phase 3A | Complete | Intelligence layer — UIR, symbols, types, CFG, DFG, IPA |
| Phase 3B.1 | Complete | Multi-agent runtime — DAG scheduler, message bus, state |
| Phase 3B.2 | Complete | Knowledge systems — semantic memory, repo graph, model router |
| Phase 3C.1 | Complete | Code intelligence engine — all program graph builders |
| Phase 3C.2 | Complete | Bug detection and reasoning — 15 modules, evidence/confidence/root cause |
| Phase 3D.1 | Complete | Autonomous patch generation — 14 modules, 104 tests |
| Phase 3D.2 | Complete | Autonomous patch validation — 20 modules, 132 total tests |
| Phase 3D.3 | Complete | Autonomous multi-agent repair loop — 18 modules, 252 total tests |

---

## License

MIT License — see `LICENSE` for details.

# K Sai Sri Harsha(Author)
