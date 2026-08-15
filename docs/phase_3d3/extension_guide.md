# Phase 3D.3 — Subsystem Extension Guide

ARGUS is designed to be extensible. You can register custom agents, refinement strategies, and repair policies without modifying existing core classes.

---

## 1. Adding a New Repair Agent

To add a new agent, inherit from `BaseRepairAgent` and define the agent role:

```python
from backend.core.autonomous_repair.agents.base_agent import BaseRepairAgent
from backend.core.autonomous_repair.repair_models import AgentDecision, AgentRole

class SecurityAuditorAgent(BaseRepairAgent):
    role = AgentRole.REVIEWER  # Or define a new AgentRole enum member
    
    async def _run(self, context: dict) -> AgentDecision:
        patch_diff = context.get("patch_diff", "")
        # Run custom security auditing code or LLM calls...
        return AgentDecision(
            agent_role=self.role,
            iteration=context.get("iteration", 0),
            reasoning="No OWASP violations found in patch diff.",
            confidence=0.95
        )
```

Register your agent with the manager before running the orchestrator:
```python
orchestrator = RepairOrchestrator(provider, config)
# Overwrite the default reviewer
orchestrator._agents.register_agent(AgentRole.REVIEWER, SecurityAuditorAgent(provider, config))
```

---

## 2. Registering a Custom Repair Policy

Implement `BaseRepairPolicy` and register it with the policy registry:

```python
from backend.core.autonomous_repair.policy import BaseRepairPolicy, RepairPolicyRegistry
from backend.core.autonomous_repair.configuration import RepairConfiguration

class UltraStrictPolicy(BaseRepairPolicy):
    def configure(self) -> RepairConfiguration:
        return RepairConfiguration(
            max_iterations=4,
            acceptance_threshold=0.95,  # Almost perfect score required
            temperature=0.05
        )

# Register policy
RepairPolicyRegistry.register("strict", UltraStrictPolicy())

# Resolve config later
config = RepairPolicyRegistry.get_config("strict")
```
