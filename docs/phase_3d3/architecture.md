# Phase 3D.3 — Autonomous Multi-Agent Repair Loop Architecture

## Overview

The Autonomous Multi-Agent Repair Loop subsystem is designed to orchestrate the existing Phase 3D.1 **Patch Generation Engine** and Phase 3D.2 **Patch Validation Engine** into a unified, feedback-driven, closed-loop repair pipeline. 

Instead of generating patches statically, the system executes an iterative loop that:
1. Generates or refines candidates based on previous validation feedback.
2. Performs automated C++ syntax verification, compilation, static analysis re-running, and regression test suites inside isolated workspaces.
3. Incorporates multi-agent peer reviews to rate maintainability and risk.
4. Synthesizes error logs and delta metrics into strategic instructions for the next loop cycle.

---

## Design Patterns & Core Principles

The codebase strictly adheres to **SOLID** principles, typing standards, and standard enterprise design patterns:

### 1. Strategy Pattern
* **`RepairPolicy`** (`policy.py`): Decouples parameter configuration profiles (`Default`, `Conservative`, `Aggressive`) from the execution loop.
* **`TerminationPolicy`** (`termination.py`): Encapsulates different condition checks (Timeout, Convergence, Accepted, Iteration limits) as pluggable criteria.
* **`ConvergenceDetector`** (`convergence.py`): Evaluates progress plateaus via absolute/relative delta or window variance checks.

### 2. Factory / Registry Pattern
* **`AgentManager`** (`agent_manager.py`): Acts as a centralized registry and lifecycle manager. It lazily instantiates, caches, and hot-swaps specialized agents (Validator, Generator, Reviewer, Planner, Report).
* **`RepairPolicyRegistry`** (`policy.py`): Resolves configurable policies dynamically by string identifiers.

### 3. Observer Pattern
* **`AuditTrail`** (`audit.py`): Subscribes to events published by all orchestration components, logging chronological transitions in an append-only JSONL event stream to guarantee full session reproducibility.

### 4. Dependency Injection
* Every component requires its dependencies (e.g. `BaseLLMProvider`, `RepairConfiguration`, state modules) to be injected via constructors. This makes components highly testable, mockable, and decoupling implementation details.

---

## Subsystem Architecture Details

The system consists of the following packages and directories:

* **`backend/core/autonomous_repair/`**
  * `exceptions.py`: custom hierarchy extending `RepairLoopError`.
  * `configuration.py`: Pydantic V2 model mapping loop parameters.
  * `repair_models.py`: schemas representing StructuredFeedback, AgentDecision, RepairIteration, and RepairSession.
  * `candidate_pool.py`: thread-safe in-memory pool tracking candidates and parent-child lineage.
  * `memory.py`: sliding-window context buffer for loop iterations.
  * `audit.py`: event logger for session replaying.
  * `metrics.py`: runtime aggregator tracking execution timelines, compiler counts, and LLM completions.
  * `scoring.py`: multi-signal scoring algorithm weighing validator outputs, confidence, risk, and maintainability.
  * `feedback_engine.py`: regex pattern matchers generating remediation actions from raw compilation/regression logs.
  * `reasoning_engine.py`: LLM-backed failure reason analyzer.
  * `refinement_engine.py`: strategy-driven patch optimizer.
  * `planning.py`: rule-based strategy mapping engine.
  * `repair_loop.py`: internal controller managing the iterative execution state machine.
  * `orchestrator.py`: top-level API entry point.
