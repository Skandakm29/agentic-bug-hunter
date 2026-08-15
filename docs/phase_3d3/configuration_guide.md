# Phase 3D.3 — Configuration & Tuning Guide

The multi-agent repair loop behavior can be customized via the `RepairConfiguration` model. The settings below detail the configuration options, default values, and target tuning scenarios.

| Parameter Name | Type | Default | Description |
|---|---|---|---|
| `max_iterations` | `int` | `5` | Maximum number of iterations to execute before stopping. |
| `timeout_seconds` | `float` | `300.0` | Session timeout in seconds. |
| `convergence_window` | `int` | `2` | Iterations to monitor for plateaus before terminating. |
| `convergence_delta` | `float` | `0.01` | Minimum required improvement to reset the plateau window. |
| `acceptance_threshold` | `float` | `0.75` | Minimum score required to accept a patch. |
| `temperature` | `float` | `0.2` | Creativity level for generator completions. |
| `reasoning_temperature` | `float` | `0.1` | Creativity level for reasoning engine (lower = more deterministic). |
| `refinement_temperature` | `float` | `0.25` | Creativity level for refinement attempts. |
| `prefer_refinement` | `bool` | `True` | Prioritize refining existing patches rather than starting from scratch. |
| `candidates_per_iteration` | `int` | `2` | Number of patch candidates to request per generation cycle. |

---

## Tuning Profiles

You can select standard policy configurations dynamically through the `RepairPolicyRegistry` or subclass `BaseRepairPolicy`:

### 1. Conservative (Fast, low cost, low risk)
Used for critical C++ codebase patches where regression safety is prioritized over complex structural refactoring:
```python
config = RepairPolicyRegistry.get_config("conservative")
# max_iterations = 3
# acceptance_threshold = 0.85
# temperature = 0.1
# planner agent disabled
```

### 2. Aggressive (Deep exploration)
Best suited for complex logic bugs, memory leaks, and thread-safety concurrency issues:
```python
config = RepairPolicyRegistry.get_config("aggressive")
# max_iterations = 8
# acceptance_threshold = 0.65
# temperature = 0.4
# prefer_refinement = False
```
