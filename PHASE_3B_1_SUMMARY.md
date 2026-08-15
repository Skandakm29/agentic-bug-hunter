# Phase 3B.1 Summary: Enterprise Static Analysis Engine, Rule Engine & MCP Architecture Transformation

This document serves as the single source of truth for the **Agentic Bug Hunter** platform's completed Phase 3B.1 code intelligence and MCP transformation.

---

## 1. Static Analysis Platform Transformation Summary

The codebase has been refactored from a collection of hardcoded regex checking scripts into a modular, language-agnostic **Static Analysis Platform**.

### Core Architecture Goals Achieved:
* **Intermediate Representation (IR):** Decoupled the parsing stage from the rule checking stage by introducing a unified `CodeRepresentation` intermediate representation.
* **Pluggable Rules Engine:** Refactored static checks into self-contained rule classes inheriting from a central `BaseRule` class. Rules are registered dynamically and queryable by language.
* **Separation of Parsing and Checking:** The core analysis engine now handles pipeline orchestration and execution tracing, while language parsers and rule registries implement specific code logic.
* **MCP Decoupling:** Separated FastMCP transport routes from core execution logic by introducing an MCP Tool Registry and execution coordinator.

---

## 2. End-to-End Analysis Pipeline

The static analysis pipeline runs code analysis through the following phases:

```
[User C++ Source Code]
          │
          ▼
[1. Parser Registry Resolution] (ParserRegistry maps file extension to active CppParser)
          │
          ▼
[2. IR AST Parsing & Normalization] (CppParser builds CodeLine list, pre-computing comments & ISR depths)
          │
          ▼
[3. CodeRepresentation IR Generation] (Generates structured token lists and file metadata)
          │
          ▼
[4. Rules Registry Discovery] (RuleRegistry queries registered classes matching target language)
          │
          ▼
[5. Rule Execution Loop] (Iterates rules in sequence over CodeRepresentation, appending findings)
          │
          ▼
[6. Findings Normalization] (Standardizes all errors into the NormalizedFinding Pydantic schema)
          │
          ▼
[7. Metrics Logging & Telemetry] (Records rule durations, parser latencies, and counts)
```

---

## 3. Rule Engine Architecture

### Rule Framework & Lifecycle
Each check is a standalone class inheriting from the abstract `BaseRule` class ([backend/core/analysis/rules/base.py](file:///c:/Users/harsh/Desktop/agentic_ai_bug%20hunter/agentic-bug-hunter/backend/core/analysis/rules/base.py)). 

Rules follow a clean lifecycle:
1. **Registration:** Classes call `RuleRegistry.register_rule` upon import.
2. **Instantiation:** Instantiated at runtime by the `AnalysisEngine` orchestrator.
3. **Execution:** The `execute(representation)` method checks code structures and compiles matched warnings.

### Rule Metadata Schema
Rules define explicit properties:
* `rule_id` (Unique tag mapping)
* `name` (Human-readable label)
* `description` (Detailed violation description)
* `category` (Syntax Error, API Misuse, Memory Safety, Performance, etc.)
* `severity` (CRITICAL, HIGH, MEDIUM, LOW)
* `confidence` (Static check certainty multiplier)

### Severity & Confidence Scoring Models
* **Severity Matrix:** Rules define baseline priorities. Syntactic failures (e.g. unmatched blocks) are marked `CRITICAL`, while style/API recommendations are marked `MEDIUM`.
* **Confidence Propagation:** The orchestrator combines the static rule confidence with the LLM validation output using a weighted sum formula:
  $$\text{Combined Confidence} = 0.6 \times \text{LLM\_Confidence} + 0.4 \times \text{Static\_Confidence}$$

### False-Positive Reduction Strategy
* **Comment Filtering:** Code lines flagged as comments (`line.is_comment` is True) are automatically skipped by rules.
* **Context Verification:** Nested curly braces tracking ensures checks like `BlockingDelayRule` only run within ISR contexts, preventing false matches in helper sleep functions.
* **Multi-Signal Verification:** The final orchestrator invokes semantic AI validation on the highest-confidence static anomaly before presenting it to the developer.

---

## 4. MCP & Tool Framework

The Model Context Protocol (MCP) has been refactored into a reusable capability platform:

```
[FastMCP Server] (Transport interface SSE wrapper)
       │
  Invokes Tool
       │
       ▼
[MCPCoordinator] (Handles async wait_for timeouts, logs latency, validates schemas)
       │
       ▼
[MCPToolRegistry] (Resolves tool bindings and fetches timeout config defaults)
       │
       ▼
[backend/core/mcp/tools.py] (Executes core CSV loops or orchestrators)
```

### Key Framework Components:
1. **Tool Registry (`MCPToolRegistry`):** Declares and houses tool metadata (names, versions, schemas, default timeouts).
2. **Tool Execution Coordinator (`MCPCoordinator`):** Manages invocation lifecycles, executes tasks inside `asyncio.wait_for` timeout pools, logs execution times, and handles exceptions.
3. **Transport Decoupling:** `mcp_server.py` acts strictly as an entry runner, referencing registry wrappers to keep transport layers thin and maintainable.

---

## 5. AI-Assisted Analysis Integration

The platform defines strict boundaries between deterministic rules checking and AI reasoning:

* **Static Analysis Engine:**
  * Responsibilities: File tokenization, syntax tracking, and pattern-based rule execution.
  * Inputs: Raw code string.
  * Outputs: Structured list of `NormalizedFinding` instances.
* **AI reasoning Agents:**
  * Responsibilities: Validate if static warnings correspond to semantic bugs, generate explanations, write corrected code lines, and format the final developer audit.
  * Inputs: Target warning context and source code.
  * Outputs: Validated Pydantic models.

---

## 6. Engineering Decision Records

### EDR 1: Intermediate CodeRepresentation (IR)
* **Decision:** Introduce a lightweight parsed `CodeRepresentation` model.
* **Problem:** Rule scripts were performing redundant string slicing and scope scans.
* **Chosen Solution:** A stateful C++ parser mapping lines, comments, and nesting states into a typed IR schema.
* **Benefits:** Decouples rules from AST files and improves execution performance.

### EDR 2: Dynamic Rules Registry
* **Decision:** Decouple rule classes from execution logic.
* **Problem:** Adding a rule previously required updating `static_engine.py` functions and orchestrator tables.
* **Chosen Solution:** A central registry class mapping active checks by language extension.
* **Benefits:** Meets the Open/Closed Principle. Rules are self-contained and modular.

### EDR 3: MCP Tool Registry & Execution Coordinator
* **Decision:** Standardize tool declarations and lifecycles.
* **Problem:** FastMCP tools directly executed logic and lacked error handling or timeout parameters.
* **Chosen Solution:** An MCP coordinator wrapping execution in `asyncio.wait_for` timeout blocks.
* **Benefits:** Resolves resource leakages and isolates transport bindings.

---

## 7. Validation Report

All validation scripts and unit tests completed successfully:

1. **Syntax Checking:** Compiles all refactored Python modules successfully.
2. **Static Engine Regression Suite:** Zero-dependency unittest suite executed and passed:
   ```bash
   python -m unittest backend/tests/test_static_engine.py
   # Output: Ran 5 tests in 0.002s -- OK
   ```
3. **FastAPI Endpoints Verification:** Started the API server and validated endpoints manually:
   * GET `/rules` dynamically fetches all active rules from the `RuleRegistry`.
   * POST `/analyze` processes code, returns matches, and integrates combined scoring.
4. **Backward Compatibility:** All existing frontend fetch endpoints and MCP schemas remain fully functional.
