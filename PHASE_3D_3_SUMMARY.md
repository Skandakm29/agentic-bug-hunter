# Phase 3D.3 — Autonomous Multi-Agent Repair Loop

## Subsystem Overview

We have successfully designed and built the complete autonomous repair loop under `backend/core/autonomous_repair/`. The subsystem closes the loop between **Patch Generation (3D.1)** and **Patch Validation (3D.2)**. It enables the system to iteratively generate, evaluate, learn from failures, refine, score, and select the highest-quality candidate repair.

---

## Architectural Layout

```
backend/core/autonomous_repair/
├── __init__.py           # Unified exports
├── exceptions.py         # Subsystem error models
├── configuration.py      # Bounded, validated loop parameters
├── repair_models.py      # Pydantic schemas for feedback/decisions/timeline
├── candidate_pool.py     # Thread-safe pool tracking lineage
├── memory.py             # Chronological context manager
├── audit.py              # Log/Replay engine
├── metrics.py            # Execution statistics provider
├── scoring.py            # Multi-dimensional composite scorer
├── convergence.py        # Plateau-detection algorithms
├── termination.py        # Stop conditions evaluator
├── feedback_engine.py    # Pattern matchers for compiler/regression logs
├── reasoning_engine.py   # LLM-backed failure advisor
├── refinement_engine.py  # Minimal, targeted patch optimizer
├── planning.py           # Decision engine selecting repair steps
├── policy.py             # Strategy presets (Default, Conservative, Aggressive)
├── repair_loop.py        # Pipeline execution coordinator
└── orchestrator.py       # Main public entry point
```

---

## Implementation Details

### 1. Specialized Cooperative Agents
* **Validator Agent**: Interprets compilation failures, syntax issues, and regression results. Outlines precise code sections that must remain unchanged to preserve existing code functionality.
* **Patch Generator Agent**: Proposes syntactically style-preserved candidate patch repairs by wrapping the existing Phase 3D.1 generation engine.
* **Reviewer Agent**: Performs independent peer reviews on patch candidates, scoring them on cyclomatic complexity, readability, and regression risks.
* **Planner Agent**: Evaluates score progressions and feedback loop summaries to determine next actions (e.g. refine candidate, generate new, escalate, or stop).
* **Report Agent**: Autonomously compiles chronological engineering summaries detailing the winning candidate selection rationale, score history, and lineage.

### 2. Feedback & Refinement Loop
* **Feedback Engine**: Categorizes raw test errors, assertions, or segmentation faults into structured hints.
* **Reasoning Engine**: Uses low-temperature completions to extract nuanced code repair guidance from the structured feedback.
* **Refinement Engine**: Implements 8 strategies (e.g. smart pointers, exception safety, bounds checking, null checks) to refine existing patches rather than starting from scratch.

### 3. Loop Termination & Convergence Control
* **Scoring Engine**: Evaluates a weighted composite score spanning validation metrics, confidence, simplicity, maintainability, and static re-analysis improvements.
* **Convergence Detector**: Evaluates score variance or absolute change across a sliding window to detect plateaus.
* **Termination Policy**: Monistors and triggers loop exit under 7 conditions (Accepted winner, iteration limit, timeout, repeated failures, pool exhaustion, convergence, manual stop).

---

## Verification & Testing

* Created a comprehensive, self-contained test file: [test_autonomous_repair.py](file:///c:/Users/harsh/Desktop/Argus_desktop/Argus/backend/tests/test_autonomous_repair.py)
* **Test Suite**: Covers 15 test classes checking exceptions, configs, memory eviction, lineage tracking, audit trails, metrics snapshots, composite scoring, convergence plateaus, termination checks, regex feedback parsing, LLM completion mocks, and complete repair loop executions.

---

## Documentation Added

All documentation is located under `docs/phase_3d3/`:
1. [architecture.md](file:///c:/Users/harsh/Desktop/Argus_desktop/Argus/docs/phase_3d3/architecture.md)
2. [repair_loop_diagram.md](file:///c:/Users/harsh/Desktop/Argus_desktop/Argus/docs/phase_3d3/repair_loop_diagram.md)
3. [agent_interaction.md](file:///c:/Users/harsh/Desktop/Argus_desktop/Argus/docs/phase_3d3/agent_interaction.md)
4. [configuration_guide.md](file:///c:/Users/harsh/Desktop/Argus_desktop/Argus/docs/phase_3d3/configuration_guide.md)
5. [extension_guide.md](file:///c:/Users/harsh/Desktop/Argus_desktop/Argus/docs/phase_3d3/extension_guide.md)
6. [api_reference.md](file:///c:/Users/harsh/Desktop/Argus_desktop/Argus/docs/phase_3d3/api_reference.md)
