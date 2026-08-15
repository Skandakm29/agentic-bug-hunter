"""
Autonomous Repair Loop — Agent Manager  (Phase 3D.3)
=====================================================
``AgentManager`` is a factory and registry for all repair agents.

Design invariants
-----------------
- Agents are instantiated lazily on first access and cached.
- Any agent can be hot-swapped via ``register_agent()`` without
  restarting the session.
- Disabled agents (``config.agents_enabled[role] = False``) raise
  ``LookupError`` on access so callers can skip gracefully.
- ``stats()`` returns per-agent execution statistics for observability.
"""
from __future__ import annotations

import logging
from typing import Dict, Optional, Type

from backend.core.ai.providers.base import BaseLLMProvider
from backend.core.autonomous_repair.agents.base_agent import BaseRepairAgent
from backend.core.autonomous_repair.agents.patch_generator_agent import PatchGeneratorAgent
from backend.core.autonomous_repair.agents.planner_agent import PlannerAgent
from backend.core.autonomous_repair.agents.report_agent import ReportAgent
from backend.core.autonomous_repair.agents.reviewer_agent import ReviewerAgent
from backend.core.autonomous_repair.agents.validator_agent import ValidatorAgent
from backend.core.autonomous_repair.configuration import RepairConfiguration
from backend.core.autonomous_repair.repair_models import AgentRole

logger = logging.getLogger("backend.autonomous_repair.agent_manager")

_ROLE_CLASS_MAP: Dict[AgentRole, Type[BaseRepairAgent]] = {
    AgentRole.VALIDATOR:       ValidatorAgent,
    AgentRole.PATCH_GENERATOR: PatchGeneratorAgent,
    AgentRole.REVIEWER:        ReviewerAgent,
    AgentRole.PLANNER:         PlannerAgent,
    AgentRole.REPORT:          ReportAgent,
}


class AgentManager:
    """
    Factory and registry for autonomous repair agents.

    All agents share the same ``BaseLLMProvider`` and ``RepairConfiguration``
    instances (injected at construction time).  Individual agents can be
    replaced at runtime without affecting the others.
    """

    def __init__(
        self,
        provider: BaseLLMProvider,
        config: Optional[RepairConfiguration] = None,
    ) -> None:
        self._provider = provider
        self._cfg = config or RepairConfiguration()
        self._cache: Dict[AgentRole, BaseRepairAgent] = {}
        self._overrides: Dict[AgentRole, BaseRepairAgent] = {}

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    def get_agent(self, role: AgentRole) -> BaseRepairAgent:
        """
        Return the agent instance for the given role.

        Raises
        ------
        LookupError
            If the agent is disabled in the configuration.
        """
        role_key = role.value
        enabled = self._cfg.agents_enabled.get(role_key, True)
        if not enabled:
            raise LookupError(
                f"Agent '{role_key}' is disabled in RepairConfiguration."
            )

        # Check for runtime override
        if role in self._overrides:
            return self._overrides[role]

        # Lazy-create and cache
        if role not in self._cache:
            agent_class = _ROLE_CLASS_MAP.get(role)
            if agent_class is None:
                raise LookupError(f"No agent class registered for role '{role.value}'.")
            agent = agent_class(provider=self._provider, config=self._cfg)
            self._cache[role] = agent
            logger.debug("AgentManager: instantiated %s.", agent_class.__name__)

        return self._cache[role]

    def is_enabled(self, role: AgentRole) -> bool:
        """Return True if the agent for this role is enabled."""
        return self._cfg.agents_enabled.get(role.value, True)

    # ------------------------------------------------------------------
    # Runtime registration
    # ------------------------------------------------------------------

    def register_agent(self, role: AgentRole, agent: BaseRepairAgent) -> None:
        """
        Hot-swap an agent instance at runtime.

        The existing cache entry (if any) is discarded so the new instance
        is returned on the next ``get_agent()`` call.

        Parameters
        ----------
        role : The role to override.
        agent : A fully-constructed ``BaseRepairAgent`` instance.
        """
        self._overrides[role] = agent
        self._cache.pop(role, None)
        logger.info(
            "AgentManager: registered custom agent %s for role '%s'.",
            type(agent).__name__,
            role.value,
        )

    def unregister_agent(self, role: AgentRole) -> None:
        """Remove a runtime override; the default agent class is used again."""
        self._overrides.pop(role, None)
        self._cache.pop(role, None)

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------

    def stats(self) -> Dict[str, dict]:
        """Return per-agent execution statistics."""
        result: Dict[str, dict] = {}
        for role, agent in {**self._cache, **self._overrides}.items():
            result[role.value] = agent.stats
        return result
