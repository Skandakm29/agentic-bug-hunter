"""
Autonomous Repair Loop — Repair Policies  (Phase 3D.3)
======================================================
``RepairPolicy`` establishes standard configuration presets and behaviors
for different repair scenarios. It implements the Strategy pattern, allowing
different repair behaviors to be selected based on the priority or nature
of the target bug.

Policies supported:
1. DefaultRepairPolicy      — standard balanced settings.
2. ConservativeRepairPolicy  — minimal changes, low temperature, fewer iterations.
3. AggressiveRepairPolicy    — complex fixes, higher temperature, more iterations,
                              uses standard styling instead of conservative.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, Any

from backend.core.autonomous_repair.configuration import RepairConfiguration
from backend.core.patch_generation.patch_models import RepairStyle


class BaseRepairPolicy(ABC):
    """
    Abstract base class for all Repair Policies.
    """

    @abstractmethod
    def configure(self) -> RepairConfiguration:
        """
        Produce a customized RepairConfiguration mapping to the policy strategy.
        """
        pass


class DefaultRepairPolicy(BaseRepairPolicy):
    """
    Standard balanced repair policy for general usage.
    """

    def configure(self) -> RepairConfiguration:
        return RepairConfiguration(
            max_iterations=5,
            acceptance_threshold=0.75,
            temperature=0.2,
            prefer_refinement=True
        )


class ConservativeRepairPolicy(BaseRepairPolicy):
    """
    Risk-averse policy targeting minor, high-confidence changes.
    """

    def configure(self) -> RepairConfiguration:
        return RepairConfiguration(
            max_iterations=3,
            acceptance_threshold=0.85,  # Requires high score
            temperature=0.1,            # Less creative
            prefer_refinement=True,
            candidates_per_iteration=1,
            agents_enabled={
                "validator": True,
                "patch_generator": True,
                "reviewer": True,
                "planner": False,      # Skip planner agent to save time
                "report": True,
            }
        )


class AggressiveRepairPolicy(BaseRepairPolicy):
    """
    Policy geared towards complex repairs that might require structural changes.
    """

    def configure(self) -> RepairConfiguration:
        return RepairConfiguration(
            max_iterations=8,           # More chances to converge
            acceptance_threshold=0.65,  # Slightly lower threshold for difficult bugs
            temperature=0.4,            # More creative exploration
            prefer_refinement=False,    # Try generating fresh ideas often
            candidates_per_iteration=3,
            reasoning_temperature=0.2
        )


class RepairPolicyRegistry:
    """
    Registry for obtaining concrete RepairPolicy configurations by name.
    """

    _registry: Dict[str, BaseRepairPolicy] = {
        "default": DefaultRepairPolicy(),
        "conservative": ConservativeRepairPolicy(),
        "aggressive": AggressiveRepairPolicy()
    }

    @classmethod
    def get_config(cls, policy_name: str) -> RepairConfiguration:
        """
        Get the config for a named policy. Defaults to DefaultRepairPolicy if not found.
        """
        policy = cls._registry.get(policy_name.lower(), cls._registry["default"])
        return policy.configure()

    @classmethod
    def register(cls, name: str, policy: BaseRepairPolicy) -> None:
        """
        Register a custom repair policy at runtime.
        """
        cls._registry[name.lower()] = policy
