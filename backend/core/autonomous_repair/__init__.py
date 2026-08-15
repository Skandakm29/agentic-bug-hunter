"""
Autonomous Multi-Agent Repair Loop Subsystem  (Phase 3D.3)
==========================================================
Orchestrates Phase 3D.1 Patch Generation and Phase 3D.2 Patch Validation
into an autonomous, iterative closed-loop repair framework.
"""

from backend.core.autonomous_repair.exceptions import RepairLoopError
from backend.core.autonomous_repair.configuration import RepairConfiguration
from backend.core.autonomous_repair.orchestrator import RepairOrchestrator
from backend.core.autonomous_repair.repair_models import (
    RepairSession,
    RepairReport,
    RepairIteration,
    RepairStrategy,
    RefinementStrategy,
    AgentRole,
    TerminationReason,
)
from backend.core.autonomous_repair.policy import (
    BaseRepairPolicy,
    DefaultRepairPolicy,
    ConservativeRepairPolicy,
    AggressiveRepairPolicy,
    RepairPolicyRegistry,
)

__all__ = [
    "RepairLoopError",
    "RepairConfiguration",
    "RepairOrchestrator",
    "RepairSession",
    "RepairReport",
    "RepairIteration",
    "RepairStrategy",
    "RefinementStrategy",
    "AgentRole",
    "TerminationReason",
    "BaseRepairPolicy",
    "DefaultRepairPolicy",
    "ConservativeRepairPolicy",
    "AggressiveRepairPolicy",
    "RepairPolicyRegistry",
]
