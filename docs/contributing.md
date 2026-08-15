# Contributor & Quality Guidelines

This document outlines the coding standards, branch rules, and code quality workflows for all contributors to the **Agentic Bug Hunter** codebase.

---

## 1. Branch Strategy

* **`main`** — Production branch. Only stable, fully tested, and reviewed code should be merged here.
* **Feature Branches (`feat/...`)** — For implementing new validation rules, UI features, or enhancements.
* **Bugfix Branches (`fix/...`)** — For bug corrections or static analyzer false-positive patches.

---

## 2. Code Quality & Styling Standards

Every python module should be readable and consistently styled. 

### Python Guidelines:
* Follow PEP 8 guidelines.
* Run a formatter (e.g. `black` or `ruff`) before committing code to format spacing and formatting styles.
* Use explicit type hints for helper functions and orchestrator interfaces to ensure readability.
* Maintain clear docstring explanations on all public classes, functions, and router endpoints.

### TypeScript / React Guidelines:
* Use functional React components with proper TypeScript prop types.
* Keep components modular and single-purpose. Export UI logic into reusable elements.
* Style using CSS Modules (`.module.css`) to prevent stylesheet leaks.

---

## 3. Pull Request (PR) Policy

Before submitting a Pull Request for review:
1. Ensure Python syntax compiles successfully without errors:
   ```bash
   python -m py_compile backend/main.py backend/mcp_server.py
   ```
2. Verify that all static engine tests pass without fail:
   ```bash
   python -m unittest backend/tests/test_static_engine.py
   ```
3. Test your changes manually inside the UI to ensure no existing functionality is broken.
