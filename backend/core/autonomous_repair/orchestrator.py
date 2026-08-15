"""
Autonomous Repair Loop — Orchestrator  (Phase 3D.3)
===================================================
``RepairOrchestrator`` is the primary public entry point for the autonomous
repair loop subsystem. It coordinates initialization, dependency injection,
execution, reporting, and session packaging.

Usage:
------
    orchestrator = RepairOrchestrator(provider, config)
    session = await orchestrator.run(
        finding=finding,
        code=code,
        original_code_path=path
    )
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, Optional

from backend.core.ai.providers.base import BaseLLMProvider
from backend.core.autonomous_repair.agent_manager import AgentManager
from backend.core.autonomous_repair.agents.report_agent import ReportAgent
from backend.core.autonomous_repair.audit import AuditTrail
from backend.core.autonomous_repair.candidate_pool import CandidatePool
from backend.core.autonomous_repair.configuration import RepairConfiguration
from backend.core.autonomous_repair.memory import RepairMemory
from backend.core.autonomous_repair.metrics import RepairMetricsCollector
from backend.core.autonomous_repair.repair_loop import RepairLoop
from backend.core.patch_validation.configuration import PatchValidationConfig
from backend.core.autonomous_repair.repair_models import (
    AgentRole,
    RepairSession,
    TerminationReason,
)

logger = logging.getLogger("backend.autonomous_repair.orchestrator")


class RepairOrchestrator:
    """
    Orchestrates the lifecycle of an autonomous repair session. Wires all
    collaborators together and yields the final RepairSession model.
    """

    def __init__(
        self,
        provider: BaseLLMProvider,
        config: Optional[RepairConfiguration] = None,
        validation_config: Optional[PatchValidationConfig] = None,
    ) -> None:
        """
        Parameters
        ----------
        provider : Any ``BaseLLMProvider`` implementation.
        config : Loop behaviour — iterations, thresholds, agents, weights.
        validation_config : Toolchain settings handed to the Phase 3D.2
            ``ValidationEngine`` (compiler, build system, whether regression
            and static reanalysis run). Defaults to cmake + gcc; override it
            to match the target project or no candidate will compile.
        """
        self._provider = provider
        self._cfg = config or RepairConfiguration()
        self._validation_cfg = validation_config

    async def run(
        self,
        finding: Any,
        code: str,
        root_cause: Optional[Any] = None,
        evidence: Optional[Any] = None,
        original_code_path: str = "",
        bug_id: Optional[str] = None,
    ) -> RepairSession:
        """
        Runs the autonomous repair loop on a target finding.

        Parameters
        ----------
        finding : NormalizedFinding (from Phase 1 / 3A).
        code : String content of the target file.
        root_cause : Optional RootCauseChain analysis result.
        evidence : Optional EvidenceGraph.
        original_code_path : Root directory path of the target code base.
        bug_id : Optional unique string identifier for the bug.

        Returns
        -------
        ``RepairSession`` containing candidate records, report, audit log,
        and loop termination metadata.
        """
        t_start = time.time()
        session_id = str(uuid.uuid4())
        
        # Standardize bug_id and rule_id
        resolved_bug_id = bug_id or getattr(finding, "bug_id", None) or f"BUG-{session_id[:8].upper()}"
        rule_id = getattr(finding, "rule_id", "UNKNOWN-RULE")

        logger.info(
            "Initializing Autonomous Repair Session %s for Bug %s",
            session_id,
            resolved_bug_id,
        )

        # 1. State and Diagnostics Instantiation
        pool = CandidatePool()
        memory = RepairMemory(max_entries=self._cfg.max_memory_entries)
        audit = AuditTrail(session_id=session_id, enabled=self._cfg.enable_audit_trail)
        metrics = RepairMetricsCollector(session_id=session_id)

        # 2. Agent Manager Factory
        agents = AgentManager(self._provider, self._cfg)

        # 3. Assemble Repair Loop
        loop = RepairLoop(
            provider=self._provider,
            config=self._cfg,
            agent_manager=agents,
            candidate_pool=pool,
            memory=memory,
            audit_trail=audit,
            metrics_collector=metrics,
            validation_config=self._validation_cfg,
        )

        # 4. Execute the Loop
        termination_reason = TerminationReason.UNKNOWN
        try:
            termination_reason = await loop.execute_loop(
                finding=finding,
                code=code,
                root_cause=root_cause,
                evidence=evidence,
                original_code_path=original_code_path,
                bug_id=resolved_bug_id,
                rule_id=rule_id,
            )
        except Exception as exc:
            logger.error("Repair loop execution crashed: %s", exc, exc_info=True)
            termination_reason = TerminationReason.REPEATED_FAILURE

        # 5. Build Final Report
        best_entry = pool.get_best()
        accepted = (termination_reason == TerminationReason.ACCEPTED) or (best_entry is not None and best_entry.accepted)

        # Initialize base session model
        t_end = time.time()
        duration_ms = (t_end - t_start) * 1000

        session = RepairSession(
            session_id=session_id,
            bug_id=resolved_bug_id,
            rule_id=rule_id,
            accepted=accepted,
            termination_reason=termination_reason,
            best_candidate_entry_id=best_entry.entry_id if best_entry else None,
            best_composite_score=best_entry.composite_score.total if best_entry and best_entry.composite_score else (best_entry.validation_score if best_entry else 0.0),
            iterations=memory.get_all(),
            started_at=t_start,
            completed_at=t_end,
            total_duration_ms=duration_ms,
            metrics_snapshot=metrics.snapshot().model_dump(),
        )

        # Generate report and attach if enabled
        if agents.is_enabled(AgentRole.REPORT):
            try:
                report_agent: ReportAgent = agents.get_agent(AgentRole.REPORT)
                report = report_agent.build_report(session, pool)
                session.report = report
            except Exception as report_exc:
                logger.error("Failed to generate repair report: %s", report_exc)

        # Attach audit trail if requested
        if self._cfg.export_audit_on_completion:
            session.audit_trail_jsonl = audit.export_jsonl()

        logger.info(
            "Repair Session %s ended. Accepted: %s, Score: %.3f, Duration: %.1fms",
            session_id,
            session.accepted,
            session.best_composite_score,
            duration_ms,
        )

        return session
