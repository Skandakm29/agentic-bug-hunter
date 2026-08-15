"""
Autonomous Repair Agents — Report Agent  (Phase 3D.3)
======================================================
``ReportAgent`` summarises the completed repair session into a
``RepairReport`` — a structured, developer-facing engineering document
explaining what happened, why the best candidate won, and the full
iteration history.

Responsibilities
----------------
- Summarise the complete repair timeline.
- Explain why the accepted candidate was chosen.
- Enumerate all agent decisions and iteration outcomes.
- Produce both a Pydantic model and a Markdown narrative.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from backend.core.autonomous_repair.agents.base_agent import BaseRepairAgent
from backend.core.autonomous_repair.candidate_pool import CandidatePool
from backend.core.autonomous_repair.repair_models import (
    AgentDecision,
    AgentRole,
    RepairReport,
    RepairSession,
    TerminationReason,
)

logger = logging.getLogger("backend.autonomous_repair.agents.report")


class ReportAgent(BaseRepairAgent):
    """
    Repair agent that generates the final engineering report for a session.
    """

    role = AgentRole.REPORT

    async def _run(self, context: Dict[str, Any]) -> AgentDecision:
        """
        Parameters in ``context``
        -------------------------
        session : RepairSession
        pool : CandidatePool
        iteration : int
        """
        session: Optional[RepairSession] = context.get("session")
        pool: Optional[CandidatePool] = context.get("pool")
        iteration: int = context.get("iteration", 0)

        if not session:
            return AgentDecision(
                agent_role=self.role,
                iteration=iteration,
                reasoning="No session provided to ReportAgent.",
                confidence=0.0,
            )

        report = self.build_report(session, pool)
        return AgentDecision(
            agent_role=self.role,
            iteration=iteration,
            reasoning=report.executive_summary,
            confidence=0.9,
            metadata={"report_id": report.report_id},
        )

    # ------------------------------------------------------------------
    # Public report builder (also callable directly by RepairOrchestrator)
    # ------------------------------------------------------------------

    def build_report(
        self,
        session: RepairSession,
        pool: Optional[CandidatePool] = None,
    ) -> RepairReport:
        """
        Build a ``RepairReport`` from a completed ``RepairSession``.

        Parameters
        ----------
        session : The completed session.
        pool : The candidate pool for lineage and evolution data.

        Returns
        -------
        A fully populated ``RepairReport``.
        """
        iterations = session.iterations
        scores = [it.best_composite_score for it in iterations]
        strategies = list({it.strategy.value for it in iterations})

        # Iteration summaries
        iter_summaries: List[str] = []
        for it in iterations:
            status = "✓ improved" if it.improved else "✗ no improvement"
            iter_summaries.append(
                f"Iteration {it.iteration_index}: strategy={it.strategy.value}, "
                f"score={it.best_composite_score:.3f} ({status})"
            )

        # Agent decision log
        decision_log: List[str] = []
        for it in iterations:
            for role, dec in it.agent_decisions.items():
                decision_log.append(
                    f"[Iter {it.iteration_index}] {role.upper()}: {dec.reasoning[:120]}"
                )

        # Candidate evolution
        lineage_summary = "N/A"
        candidate_count = 0
        accepted_count = 0
        rejected_count = 0
        if pool:
            candidate_count = pool.size
            accepted_count = pool.accepted_count
            rejected_count = pool.rejected_count
            best = pool.get_best()
            if best and best.parent_entry_id:
                chain = pool.lineage_of(best.entry_id)
                lineage_summary = (
                    f"Best candidate evolved over {len(chain)} generation(s)."
                )

        # Executive summary
        if session.accepted:
            summary = (
                f"ARGUS autonomously repaired bug '{session.bug_id}' in "
                f"{len(iterations)} iteration(s). "
                f"Accepted candidate score: {session.best_composite_score:.3f}."
            )
        else:
            reason = session.termination_reason.value if session.termination_reason else "unknown"
            summary = (
                f"ARGUS attempted to repair bug '{session.bug_id}' over "
                f"{len(iterations)} iteration(s) but did not find an accepted candidate. "
                f"Termination reason: {reason}."
            )

        # Why candidate won
        why_won = ""
        if session.accepted and session.best_candidate_entry_id and pool:
            entry = pool.get(session.best_candidate_entry_id)
            if entry:
                score = entry.composite_score
                why_won = (
                    f"Candidate {entry.candidate_id} achieved a composite score of "
                    f"{session.best_composite_score:.3f} (validation={score.validation_component:.3f}, "
                    f"confidence={score.confidence_component:.3f}, "
                    f"maintainability={score.maintainability_component:.3f})."
                ) if score else f"Candidate {entry.candidate_id} met the acceptance threshold."

        report = RepairReport(
            session_id=session.session_id,
            bug_id=session.bug_id,
            rule_id=session.rule_id,
            accepted=session.accepted,
            termination_reason=session.termination_reason or TerminationReason.UNKNOWN,
            total_iterations=len(iterations),
            best_score=session.best_composite_score,
            accepted_candidate_id=session.best_candidate_entry_id,
            executive_summary=summary,
            why_candidate_won=why_won,
            iteration_summaries=iter_summaries,
            agent_decision_log=decision_log[:50],  # Cap for report size
            score_progression=scores,
            strategies_tried=strategies,
            candidate_count=candidate_count,
            accepted_count=accepted_count,
            rejected_count=rejected_count,
            lineage_summary=lineage_summary,
            total_duration_ms=session.total_duration_ms,
        )
        return report

    def render_markdown(self, report: RepairReport) -> str:
        """
        Render a ``RepairReport`` as a GitHub-flavoured Markdown document.
        """
        status_emoji = "✅" if report.accepted else "❌"
        lines: List[str] = [
            f"# {status_emoji} ARGUS Autonomous Repair Report",
            f"",
            f"**Session:** `{report.session_id}`  ",
            f"**Bug ID:** `{report.bug_id}`  ",
            f"**Status:** {'ACCEPTED' if report.accepted else 'NOT ACCEPTED'}  ",
            f"**Termination:** `{report.termination_reason.value}`  ",
            f"**Score:** {report.best_score:.3f}  ",
            f"**Duration:** {report.total_duration_ms:.0f}ms  ",
            f"",
            f"## Executive Summary",
            f"",
            report.executive_summary,
            f"",
        ]
        if report.why_candidate_won:
            lines += [f"## Why This Candidate Won", f"", report.why_candidate_won, f""]

        lines += [
            f"## Score Progression",
            f"",
            f"| Iteration | Score |",
            f"|-----------|-------|",
        ]
        for i, score in enumerate(report.score_progression):
            lines.append(f"| {i} | {score:.4f} |")

        lines += [
            f"",
            f"## Iteration History",
            f"",
        ]
        for summary in report.iteration_summaries:
            lines.append(f"- {summary}")

        lines += [
            f"",
            f"## Strategies Used",
            f"",
        ]
        for s in report.strategies_tried:
            lines.append(f"- `{s}`")

        lines += [
            f"",
            f"## Candidate Statistics",
            f"",
            f"| Metric | Count |",
            f"|--------|-------|",
            f"| Total candidates | {report.candidate_count} |",
            f"| Accepted | {report.accepted_count} |",
            f"| Rejected | {report.rejected_count} |",
            f"",
            f"**Lineage:** {report.lineage_summary}",
        ]
        return "\n".join(lines)
