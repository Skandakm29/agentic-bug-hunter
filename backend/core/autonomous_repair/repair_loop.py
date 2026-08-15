"""
Autonomous Repair Loop — Core Loop Controller  (Phase 3D.3)
============================================================
``RepairLoop`` drives the iterative sequence:
  Generate/Refine → Validate → Score → Feed back → Reason → Plan → Repeat

It coordinates the candidate pool, feedback engines, reasoning engine,
specialized agents, convergence checkers, termination policies, metrics,
and memory.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from backend.core.ai.providers.base import BaseLLMProvider
from backend.core.autonomous_repair.agent_manager import AgentManager
from backend.core.autonomous_repair.audit import AuditTrail
from backend.core.autonomous_repair.candidate_pool import CandidatePool
from backend.core.autonomous_repair.configuration import RepairConfiguration
from backend.core.autonomous_repair.convergence import ConvergenceDetector
from backend.core.autonomous_repair.exceptions import RepairLoopError
from backend.core.autonomous_repair.feedback_engine import FeedbackEngine
from backend.core.autonomous_repair.memory import RepairMemory
from backend.core.autonomous_repair.metrics import RepairMetricsCollector
from backend.core.autonomous_repair.planning import PlanningEngine
from backend.core.autonomous_repair.reasoning_engine import ReasoningEngine
from backend.core.autonomous_repair.refinement_engine import RefinementEngine
from backend.core.autonomous_repair.repair_models import (
    AgentDecision,
    AgentRole,
    AuditEventType,
    CandidateEntry,
    CompositeScore,
    RefinementStrategy,
    RepairIteration,
    RepairStrategy,
    StructuredFeedback,
    TerminationReason,
)
from backend.core.autonomous_repair.scoring import RepairScorer
from backend.core.autonomous_repair.termination import TerminationPolicy
from backend.core.patch_generation.patch_models import (
    FilePatch,
    PatchCandidate,
    StructuredPatch,
)
from backend.core.patch_validation.configuration import PatchValidationConfig
from backend.core.patch_validation.validation_engine import ValidationEngine
from backend.core.patch_validation.validation_models import ValidationReport

logger = logging.getLogger("backend.autonomous_repair.repair_loop")


class RepairLoop:
    """
    Executes the autonomous patch improvement closed-loop cycle.
    """

    def __init__(
        self,
        provider: BaseLLMProvider,
        config: RepairConfiguration,
        agent_manager: AgentManager,
        candidate_pool: CandidatePool,
        memory: RepairMemory,
        audit_trail: AuditTrail,
        metrics_collector: RepairMetricsCollector,
        validation_config: Optional[PatchValidationConfig] = None,
    ) -> None:
        self._provider = provider
        self._cfg = config
        self._agents = agent_manager
        self._pool = candidate_pool
        self._memory = memory
        self._audit = audit_trail
        self._metrics = metrics_collector

        # Engines
        self._feedback_engine = FeedbackEngine()
        self._reasoning_engine = ReasoningEngine(provider, config)
        self._refinement_engine = RefinementEngine(provider, config)
        self._planning_engine = PlanningEngine(config)
        self._scorer = RepairScorer(config)
        self._convergence_detector = ConvergenceDetector(config)
        self._termination_policy = TerminationPolicy(config)
        # Reuse the Phase 3D.2 validation engine. The config must be
        # injectable: hard-coding the defaults pinned every repair session to
        # cmake + gcc + regression testing, so on any project with a different
        # toolchain the build step failed and no candidate could ever score
        # above the acceptance threshold.
        self._validation_engine = ValidationEngine(validation_config)

    async def execute_loop(
        self,
        finding: Any,
        code: str,
        root_cause: Any,
        evidence: Any,
        original_code_path: str,
        bug_id: str,
        rule_id: str,
    ) -> TerminationReason:
        """
        Runs the iterative autonomous repair loop until termination is triggered.
        """
        self._audit.log(AuditEventType.SESSION_START, {"bug_id": bug_id, "rule_id": rule_id})
        logger.info("Starting Autonomous Repair Loop for bug_id=%s", bug_id)

        iteration_index = 0
        consecutive_failures = 0
        accepted_winner = False

        while True:
            t_iter_start = time.perf_counter()
            self._metrics.record_iteration_start(iteration_index)
            self._audit.log(AuditEventType.ITERATION_START, {"iteration": iteration_index}, iteration=iteration_index)

            # 1. Decide Strategy (first iteration is always GENERATE_NEW)
            strategy = RepairStrategy.GENERATE_NEW
            refinement_strat: Optional[RefinementStrategy] = None
            planner_decision: Optional[AgentDecision] = None

            if iteration_index > 0:
                # Retrieve last feedback
                latest_iter = self._memory.get_latest()
                last_feedback = latest_iter.feedback if latest_iter else None
                
                # Run Planner Agent if enabled
                if self._agents.is_enabled(AgentRole.PLANNER):
                    try:
                        self._metrics.record_agent_invocation()
                        planner_agent = self._agents.get_agent(AgentRole.PLANNER)
                        # PlannerAgent reads the peer agents' decisions from
                        # its context; they live on the previous iteration's
                        # record. Omitting them left the planner prompt
                        # missing half its documented inputs.
                        prev_decisions = latest_iter.agent_decisions if latest_iter else {}
                        planner_ctx = {
                            "feedback": last_feedback,
                            "validator_decision": prev_decisions.get("validator"),
                            "reviewer_decision":  prev_decisions.get("reviewer"),
                            "reasoning_decision": prev_decisions.get("reasoning"),
                            "score_progression": self._pool.score_progression(),
                            "strategies_tried": [
                                s.value for s in self._memory.get_strategies_tried()
                            ],
                            "memory_summary": self._memory.summarize(),
                            "iteration": iteration_index,
                            "max_iterations": self._cfg.max_iterations,
                            "consecutive_failures": consecutive_failures,
                        }
                        planner_decision = await planner_agent.execute(planner_ctx)
                        self._audit.log(
                            AuditEventType.AGENT_DECISION,
                            planner_decision.model_dump(),
                            iteration=iteration_index,
                            agent_role=AgentRole.PLANNER.value,
                        )
                    except Exception as e:
                        logger.error("Planner Agent failed: %s", e)
                        self._audit.log(
                            AuditEventType.AGENT_ERROR,
                            {"agent": AgentRole.PLANNER.value, "error": str(e)},
                            iteration=iteration_index,
                        )

                strategy = self._planning_engine.decide_strategy(
                    iteration_index=iteration_index,
                    score_progression=self._pool.score_progression(),
                    feedback=last_feedback,
                    planner_decision=planner_decision,
                    strategies_tried=[s.value for s in self._memory.get_strategies_tried()],
                    consecutive_failures=consecutive_failures,
                )
                refinement_strat = self._planning_engine.select_refinement_strategy(last_feedback)

            self._audit.log(
                AuditEventType.STRATEGY_SELECTED,
                {"strategy": strategy.value, "refinement_strategy": refinement_strat.value if refinement_strat else None},
                iteration=iteration_index,
            )

            if strategy == RepairStrategy.STOP:
                logger.info("Planner strategy decided to STOP.")
                term_reason = TerminationReason.MANUAL_STOP
                break

            # 2. Candidate Generation or Refinement
            current_patches: Optional[StructuredPatch] = None
            parent_entry: Optional[CandidateEntry] = None

            if strategy == RepairStrategy.GENERATE_NEW or iteration_index == 0 or strategy == RepairStrategy.SWITCH_APPROACH:
                # Generate new candidates using Patch Generator Agent
                try:
                    self._metrics.record_agent_invocation()
                    self._metrics.record_llm_call()
                    gen_agent = self._agents.get_agent(AgentRole.PATCH_GENERATOR)
                    gen_ctx = {
                        "finding": finding,
                        "code": code,
                        "root_cause": root_cause,
                        "evidence": evidence,
                        "bug_id": bug_id,
                        "iteration": iteration_index,
                    }
                    if planner_decision:
                        gen_ctx["preserve_elements"] = planner_decision.preserve_elements
                        gen_ctx["modify_elements"] = planner_decision.modify_elements

                    gen_decision = await gen_agent.execute(gen_ctx)
                    self._audit.log(
                        AuditEventType.AGENT_DECISION,
                        gen_decision.model_dump(),
                        iteration=iteration_index,
                        agent_role=AgentRole.PATCH_GENERATOR.value,
                    )
                    # The patch engine saves the structured patch in last_patch
                    current_patches = getattr(gen_agent, "last_patch", None)
                except Exception as e:
                    logger.error("Patch Generator Agent execution failed: %s", e)
                    self._audit.log(
                        AuditEventType.AGENT_ERROR,
                        {"agent": AgentRole.PATCH_GENERATOR.value, "error": str(e)},
                        iteration=iteration_index,
                    )

            elif strategy in (RepairStrategy.REFINE_EXISTING, RepairStrategy.ESCALATE):
                # Refine the best current candidate
                parent_entry = self._pool.get_best()
                if parent_entry:
                    parent_candidate = PatchCandidate.model_validate(parent_entry.candidate_snapshot)
                    latest_iter = self._memory.get_latest()
                    feedback = latest_iter.feedback if latest_iter else None
                    if feedback and refinement_strat:
                        try:
                            self._metrics.record_llm_call()
                            refined_candidate = await self._refinement_engine.refine(
                                parent=parent_candidate,
                                feedback=feedback,
                                strategy=refinement_strat,
                                file_path=finding.file_path if hasattr(finding, "file_path") else "",
                                preserve_elements=planner_decision.preserve_elements if planner_decision else None,
                                modify_elements=planner_decision.modify_elements if planner_decision else None,
                            )
                            # Wrap into a StructuredPatch
                            file_patch = FilePatch(
                                file_path=finding.file_path if hasattr(finding, "file_path") else "",
                                candidates=[refined_candidate],
                                start_line=finding.line_number if hasattr(finding, "line_number") else None,
                            )
                            current_patches = StructuredPatch(
                                bug_id=bug_id,
                                rule_id=rule_id,
                                file_patches=[file_patch],
                            )
                            self._audit.log(
                                AuditEventType.CANDIDATE_REFINED,
                                {
                                    "parent_candidate_id": parent_candidate.candidate_id,
                                    "refined_candidate_id": refined_candidate.candidate_id,
                                    "refinement_strategy": refinement_strat.value,
                                },
                                iteration=iteration_index,
                            )
                        except Exception as e:
                            logger.error("Refinement failed: %s", e)

            # If we failed to produce candidates, record the failure and still
            # evaluate the stop conditions. Skipping straight to the next
            # iteration bypassed the termination policy entirely, so a
            # persistently failing generator (no API key, retired model,
            # provider outage) span forever -- the wall-clock timeout lives
            # inside that same check and was skipped along with it.
            if not current_patches or not current_patches.file_patches or not current_patches.file_patches[0].candidates:
                logger.warning("No candidate patch produced in iteration %d", iteration_index)
                consecutive_failures += 1

                term_reason = self._termination_policy.should_terminate(
                    iteration_index=iteration_index,
                    accepted=accepted_winner,
                    consecutive_failures=consecutive_failures,
                    score_converged=False,
                    candidate_pool_size=self._pool.size,
                )
                iter_duration = (time.perf_counter() - t_iter_start) * 1000
                self._memory.add_iteration(RepairIteration(
                    iteration_index=iteration_index,
                    strategy=strategy,
                    refinement_strategy=refinement_strat,
                    improved=False,
                    terminated=(term_reason is not None),
                    termination_reason=term_reason,
                    duration_ms=iter_duration,
                    completed_at=time.time(),
                ))
                self._metrics.record_iteration_end(
                    iteration_index=iteration_index,
                    duration_ms=iter_duration,
                    best_score=0.0,
                    improved=False,
                    strategy=strategy,
                )
                self._audit.log(
                    AuditEventType.ITERATION_END,
                    {
                        "iteration": iteration_index,
                        "duration_ms": iter_duration,
                        "improved": False,
                        "no_candidates": True,
                    },
                    iteration=iteration_index,
                )
                if term_reason is not None:
                    break
                iteration_index += 1
                continue

            file_patch = current_patches.file_patches[0]
            candidates = file_patch.candidates

            # 3. Validate Candidate(s)
            self._audit.log(
                AuditEventType.VALIDATION_STARTED,
                {"patch_id": current_patches.patch_id, "candidates_count": len(candidates)},
                iteration=iteration_index,
            )

            # Re-use the existing CandidateValidator pipeline from Phase 3D.2 via ValidationEngine
            val_report: Optional[ValidationReport] = None
            try:
                val_report = await self._validation_engine.validate_patch(
                    patch=current_patches,
                    original_code_path=original_code_path,
                )
            except Exception as e:
                logger.error("Validation engine crashed: %s", e)
                val_report = ValidationReport(
                    patch_id=current_patches.patch_id,
                    bug_id=bug_id,
                    accepted=False,
                )

            self._audit.log(
                AuditEventType.VALIDATION_COMPLETE,
                val_report.model_dump() if val_report else {},
                iteration=iteration_index,
            )

            # 4. Review, Score, and Store each candidate
            iteration_candidate_entries: List[str] = []
            best_iter_composite_score = 0.0
            best_iter_val_score = 0.0
            best_candidate_id = None
            iteration_improved = False

            # Retrieve Validator Agent reasoning if enabled
            validator_decision: Optional[AgentDecision] = None
            reviewer_decision: Optional[AgentDecision] = None

            for candidate in candidates:
                self._metrics.record_candidate()
                validation_metrics = val_report.metrics.get(candidate.candidate_id) if val_report else None
                val_score = validation_metrics.score if validation_metrics else 0.0

                # Reviewer Agent assessment
                maintainability_score = 0.5
                if self._agents.is_enabled(AgentRole.REVIEWER):
                    try:
                        self._metrics.record_agent_invocation()
                        reviewer_agent = self._agents.get_agent(AgentRole.REVIEWER)
                        rev_ctx = {
                            "candidate": candidate,
                            "file_path": file_patch.file_path,
                            "bug_description": finding.description if hasattr(finding, "description") else "",
                            "iteration": iteration_index,
                        }
                        reviewer_decision = await reviewer_agent.execute(rev_ctx)
                        self._audit.log(
                            AuditEventType.AGENT_DECISION,
                            reviewer_decision.model_dump(),
                            iteration=iteration_index,
                            agent_role=AgentRole.REVIEWER.value,
                        )
                        maintainability_score = reviewer_decision.metadata.get("maintainability_score", 0.5)
                    except Exception as e:
                        logger.error("Reviewer Agent failed: %s", e)

                # Composite score calculation
                comp_score = self._scorer.score(
                    validation_score=val_score,
                    candidate_confidence=candidate.confidence,
                    estimated_risk=candidate.estimated_risk,
                    maintainability_score=maintainability_score,
                    lines_changed=validation_metrics.lines_changed if validation_metrics else 0,
                    new_bug_count=validation_metrics.new_bug_count if validation_metrics else 0,
                )

                # Store candidate in the pool
                pool_entry = self._pool.add(
                    candidate_id=candidate.candidate_id,
                    patch_id=current_patches.patch_id,
                    iteration=iteration_index,
                    candidate_snapshot=candidate.model_dump(),
                    parent_entry_id=parent_entry.entry_id if parent_entry else None,
                    strategy_used=strategy,
                    refinement_strategy=refinement_strat,
                )
                self._pool.update_scores(pool_entry.entry_id, val_score, comp_score)
                iteration_candidate_entries.append(pool_entry.entry_id)

                # Evaluate improvement & acceptance
                is_passing = self._scorer.passes_threshold(comp_score)
                if is_passing:
                    self._pool.mark_accepted(pool_entry.entry_id)
                    accepted_winner = True
                    self._audit.log(
                        AuditEventType.CANDIDATE_ACCEPTED,
                        {"entry_id": pool_entry.entry_id, "score": comp_score.total},
                        iteration=iteration_index,
                    )
                else:
                    self._pool.mark_rejected(
                        pool_entry.entry_id,
                        [f"Score {comp_score.total} below acceptance threshold {self._cfg.acceptance_threshold}"],
                    )
                    self._audit.log(
                        AuditEventType.CANDIDATE_REJECTED,
                        {"entry_id": pool_entry.entry_id, "score": comp_score.total},
                        iteration=iteration_index,
                    )

                if comp_score.total > best_iter_composite_score:
                    best_iter_composite_score = comp_score.total
                    best_candidate_id = pool_entry.entry_id
                    best_iter_val_score = val_score

            # 5. Extract structured feedback and run failure reasoning on best candidate
            feedback: Optional[StructuredFeedback] = None
            reasoning_decision: Optional[AgentDecision] = None

            if val_report and best_candidate_id:
                best_entry = self._pool.get(best_candidate_id)
                best_cand = PatchCandidate.model_validate(best_entry.candidate_snapshot) if best_entry else None
                metrics = val_report.metrics.get(best_cand.candidate_id) if best_cand and val_report else None
                
                if metrics:
                    # Run Feedback Engine
                    prev_best = self._pool.get_best()
                    prev_best_score = prev_best.composite_score.total if prev_best and prev_best.composite_score else 0.0
                    feedback = self._feedback_engine.extract(
                        report=val_report,
                        metrics=metrics,
                        candidate_id=best_cand.candidate_id,
                        iteration=iteration_index,
                        previous_best_score=prev_best_score,
                    )
                    if best_entry:
                        self._pool.attach_feedback(best_entry.entry_id, feedback.model_dump())

                    # Run Reasoning Engine
                    try:
                        self._metrics.record_llm_call()
                        reasoning_decision = await self._reasoning_engine.reason_about_failure(
                            feedback=feedback,
                            patch=current_patches,
                            report=val_report,
                            memory_summary=self._memory.summarize(),
                            iteration=iteration_index,
                        )
                        self._audit.log(
                            AuditEventType.AGENT_DECISION,
                            reasoning_decision.model_dump(),
                            iteration=iteration_index,
                            agent_role=AgentRole.VALIDATOR.value, # Reasoning engine represents Validator Agent role
                        )
                    except Exception as e:
                        logger.error("Reasoning Engine failed: %s", e)

            # Validator Agent (if enabled) additional analysis
            if self._agents.is_enabled(AgentRole.VALIDATOR) and feedback:
                try:
                    self._metrics.record_agent_invocation()
                    val_agent = self._agents.get_agent(AgentRole.VALIDATOR)
                    val_ctx = {
                        "feedback": feedback,
                        "report": val_report,
                        "iteration": iteration_index,
                    }
                    validator_decision = await val_agent.execute(val_ctx)
                    self._audit.log(
                        AuditEventType.AGENT_DECISION,
                        validator_decision.model_dump(),
                        iteration=iteration_index,
                        agent_role=AgentRole.VALIDATOR.value,
                    )
                except Exception as e:
                    logger.error("Validator Agent failed: %s", e)

            # Determine if this iteration improved over previous best
            prev_iters = self._memory.get_all()
            if prev_iters:
                best_prev_score = max(it.best_composite_score for it in prev_iters)
                iteration_improved = (best_iter_composite_score > best_prev_score)
            else:
                iteration_improved = (best_iter_composite_score > 0.0)

            if iteration_improved:
                consecutive_failures = 0
            else:
                consecutive_failures += 1

            # Check convergence
            score_history = self._pool.score_progression()
            converged = self._convergence_detector.check(score_history)
            self._audit.log(
                AuditEventType.CONVERGENCE_CHECK,
                {"score_history": score_history, "converged": converged},
                iteration=iteration_index,
            )

            # Check termination
            term_reason = self._termination_policy.should_terminate(
                iteration_index=iteration_index,
                accepted=accepted_winner,
                consecutive_failures=consecutive_failures,
                score_converged=converged,
                candidate_pool_size=self._pool.size,
            )

            self._audit.log(
                AuditEventType.TERMINATION_CHECK,
                {"termination_reason": term_reason.value if term_reason else None},
                iteration=iteration_index,
            )

            # Record iteration details to memory
            agent_decisions_dict: Dict[str, AgentDecision] = {}
            if planner_decision:
                agent_decisions_dict["planner"] = planner_decision
            if validator_decision:
                agent_decisions_dict["validator"] = validator_decision
            if reviewer_decision:
                agent_decisions_dict["reviewer"] = reviewer_decision
            if reasoning_decision:
                agent_decisions_dict["reasoning"] = reasoning_decision

            iter_duration = (time.perf_counter() - t_iter_start) * 1000

            repair_iter = RepairIteration(
                iteration_index=iteration_index,
                strategy=strategy,
                refinement_strategy=refinement_strat,
                candidate_entry_ids=iteration_candidate_entries,
                best_candidate_id=best_candidate_id,
                best_validation_score=best_iter_val_score,
                best_composite_score=best_iter_composite_score,
                agent_decisions=agent_decisions_dict,
                feedback=feedback,
                improved=iteration_improved,
                terminated=(term_reason is not None),
                termination_reason=term_reason,
                duration_ms=iter_duration,
                completed_at=time.time(),
            )
            self._memory.add_iteration(repair_iter)

            # Update metrics
            self._metrics.record_iteration_end(
                iteration_index=iteration_index,
                duration_ms=iter_duration,
                best_score=best_iter_composite_score,
                improved=iteration_improved,
                strategy=strategy,
            )

            self._audit.log(
                AuditEventType.ITERATION_END,
                {"iteration": iteration_index, "duration_ms": iter_duration, "improved": iteration_improved},
                iteration=iteration_index,
            )

            if term_reason is not None:
                break

            iteration_index += 1

        self._audit.log(AuditEventType.SESSION_END, {"termination_reason": term_reason.value})
        return term_reason
