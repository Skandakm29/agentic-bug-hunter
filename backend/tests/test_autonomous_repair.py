import sys
from pathlib import Path
# Inject project root so imports resolve correctly
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import json
import unittest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from backend.core.autonomous_repair.exceptions import (
    RepairLoopError,
    AgentExecutionError,
    CandidatePoolExhaustedError,
    TerminationLimitError,
    ConvergenceError,
    FeedbackParseError,
    ReasoningFailureError,
)
from backend.core.autonomous_repair.configuration import RepairConfiguration
from backend.core.autonomous_repair.repair_models import (
    AgentRole,
    RepairStrategy,
    TerminationReason,
    FailureSeverity,
    AuditEventType,
    RefinementStrategy,
    StructuredFeedback,
    FailureReason,
    AgentDecision,
    CandidateEntry,
    RepairIteration,
    RepairSession,
)
from backend.core.autonomous_repair.candidate_pool import CandidatePool
from backend.core.autonomous_repair.memory import RepairMemory
from backend.core.autonomous_repair.audit import AuditTrail
from backend.core.autonomous_repair.metrics import RepairMetricsCollector, MetricsSnapshot
from backend.core.autonomous_repair.scoring import RepairScorer, CompositeScore
from backend.core.autonomous_repair.convergence import ConvergenceDetector, ConvergenceStrategy
from backend.core.autonomous_repair.termination import TerminationPolicy
from backend.core.autonomous_repair.feedback_engine import FeedbackEngine
from backend.core.autonomous_repair.reasoning_engine import ReasoningEngine
from backend.core.autonomous_repair.refinement_engine import RefinementEngine
from backend.core.autonomous_repair.planning import PlanningEngine
from backend.core.autonomous_repair.policy import (
    DefaultRepairPolicy,
    ConservativeRepairPolicy,
    AggressiveRepairPolicy,
    RepairPolicyRegistry,
)
from backend.core.autonomous_repair.agent_manager import AgentManager
from backend.core.autonomous_repair.repair_loop import RepairLoop
from backend.core.autonomous_repair.orchestrator import RepairOrchestrator
from backend.core.patch_generation.patch_models import StructuredPatch, FilePatch, PatchCandidate
from backend.core.patch_validation.validation_models import (
    ValidationReport,
    ValidationMetrics,
    Diagnostics,
)


class TestExceptions(unittest.TestCase):
    def test_repair_loop_error(self):
        err = RepairLoopError("test error", iteration=2, context={"key": "val"})
        self.assertEqual(err.message, "test error")
        self.assertEqual(err.iteration, 2)
        self.assertEqual(err.context["key"], "val")
        self.assertIn("RepairLoopError", repr(err))

    def test_agent_execution_error(self):
        err = AgentExecutionError("execution failed", agent_role="validator", original_error=ValueError("nested"))
        self.assertEqual(err.agent_role, "validator")
        self.assertIsInstance(err.original_error, ValueError)

    def test_termination_limit_error(self):
        err = TerminationLimitError("limit hit", reason="timeout")
        self.assertEqual(err.reason, "timeout")


class TestConfiguration(unittest.TestCase):
    def test_default_config(self):
        cfg = RepairConfiguration()
        self.assertEqual(cfg.max_iterations, 5)
        self.assertEqual(cfg.acceptance_threshold, 0.75)
        self.assertTrue(cfg.agents_enabled["validator"])
        self.assertTrue(cfg.prefer_refinement)

    def test_modified_config(self):
        cfg = RepairConfiguration(max_iterations=10, acceptance_threshold=0.8)
        self.assertEqual(cfg.max_iterations, 10)
        self.assertEqual(cfg.acceptance_threshold, 0.8)


class TestCandidatePool(unittest.TestCase):
    def setUp(self):
        self.pool = CandidatePool()

    def test_add_and_retrieve(self):
        self.pool.add(
            candidate_id="cand_1",
            patch_id="patch_1",
            iteration=0,
            candidate_snapshot={"some": "data"},
        )
        self.assertEqual(self.pool.size, 1)
        best = self.pool.get_best()
        self.assertIsNotNone(best)
        self.assertEqual(best.candidate_id, "cand_1")

    def test_update_scores_and_status(self):
        entry = self.pool.add(
            candidate_id="cand_1",
            patch_id="patch_1",
            iteration=0,
            candidate_snapshot={"some": "data"},
        )
        comp_score = CompositeScore(total=0.85)
        self.pool.update_scores(entry.entry_id, 0.8, comp_score)
        self.pool.mark_accepted(entry.entry_id)

        retrieved = self.pool.get(entry.entry_id)
        self.assertEqual(retrieved.validation_score, 0.8)
        self.assertEqual(retrieved.composite_score.total, 0.85)
        self.assertTrue(retrieved.accepted)
        self.assertFalse(retrieved.rejected)

    def test_get_best_ranking(self):
        entry1 = self.pool.add("cand_1", "patch_1", 0, {})
        entry2 = self.pool.add("cand_2", "patch_1", 1, {})
        
        self.pool.update_scores(entry1.entry_id, 0.6, CompositeScore(total=0.6))
        self.pool.update_scores(entry2.entry_id, 0.8, CompositeScore(total=0.8))
        self.pool.mark_accepted(entry2.entry_id)

        best = self.pool.get_best()
        self.assertEqual(best.entry_id, entry2.entry_id)

    def test_lineage(self):
        entry1 = self.pool.add("cand_1", "patch_1", 0, {})
        entry2 = self.pool.add("cand_2", "patch_1", 1, {}, parent_entry_id=entry1.entry_id)
        entry3 = self.pool.add("cand_3", "patch_1", 2, {}, parent_entry_id=entry2.entry_id)

        lineage = self.pool.lineage_of(entry3.entry_id)
        self.assertEqual(len(lineage), 3)
        self.assertEqual(lineage[0].entry_id, entry1.entry_id)
        self.assertEqual(lineage[1].entry_id, entry2.entry_id)
        self.assertEqual(lineage[2].entry_id, entry3.entry_id)

    def test_export(self):
        self.pool.add("cand_1", "patch_1", 0, {"val": 123})
        exported = self.pool.export()
        data = json.loads(exported)
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["entries"][0]["candidate_id"], "cand_1")


class TestMemory(unittest.TestCase):
    def setUp(self):
        self.mem = RepairMemory(max_entries=5)

    def test_eviction(self):
        for i in range(7):
            self.mem.add_iteration(
                RepairIteration(
                    iteration_index=i,
                    strategy=RepairStrategy.GENERATE_NEW,
                    best_composite_score=float(i) / 10.0,
                )
            )
        self.assertEqual(self.mem.size, 5)
        # Oldest index 0 and 1 should be evicted, keeping 2 to 6
        all_iters = self.mem.get_all()
        self.assertEqual(all_iters[0].iteration_index, 2)
        self.assertEqual(all_iters[-1].iteration_index, 6)

    def test_consecutive_failures(self):
        self.mem.add_iteration(RepairIteration(iteration_index=0, strategy=RepairStrategy.GENERATE_NEW, best_composite_score=0.5, improved=True))
        self.mem.add_iteration(RepairIteration(iteration_index=1, strategy=RepairStrategy.REFINE_EXISTING, best_composite_score=0.5, improved=False))
        self.mem.add_iteration(RepairIteration(iteration_index=2, strategy=RepairStrategy.REFINE_EXISTING, best_composite_score=0.5, improved=False))
        self.assertEqual(self.mem.consecutive_failures(), 2)

    def test_summarize(self):
        self.mem.add_iteration(RepairIteration(iteration_index=0, strategy=RepairStrategy.GENERATE_NEW, best_composite_score=0.5, improved=True))
        summary = self.mem.summarize()
        self.assertIn("1 iteration(s) completed", summary)
        self.assertIn("0.5", summary)


class TestAuditTrail(unittest.TestCase):
    def setUp(self):
        self.audit = AuditTrail(session_id="session_123", enabled=True)

    def test_log_and_export(self):
        self.audit.log(AuditEventType.ITERATION_START, {"iter": 0}, iteration=0)
        self.audit.log(AuditEventType.CANDIDATE_ACCEPTED, {"entry": "e1"}, iteration=0)
        self.assertEqual(self.audit.event_count, 2)

        jsonl = self.audit.export_jsonl()
        self.assertEqual(len(jsonl.splitlines()), 2)
        
        replayed = AuditTrail.replay(jsonl)
        self.assertEqual(len(replayed), 2)
        self.assertEqual(replayed[0].event_type, AuditEventType.ITERATION_START)
        self.assertEqual(replayed[1].payload["entry"], "e1")


class TestMetricsCollector(unittest.TestCase):
    def setUp(self):
        self.metrics = RepairMetricsCollector("session_1")

    def test_recording(self):
        self.metrics.record_iteration_start(0)
        self.metrics.record_llm_call(2)
        self.metrics.record_agent_invocation(3)
        self.metrics.record_candidate(accepted=True)
        self.metrics.record_iteration_end(0, 1500.0, 0.8, True, RepairStrategy.GENERATE_NEW)

        snap = self.metrics.snapshot()
        self.assertEqual(snap.total_iterations, 1)
        self.assertEqual(snap.total_llm_calls, 2)
        self.assertEqual(snap.total_agent_invocations, 3)
        self.assertEqual(snap.accepted_candidates, 1)
        self.assertEqual(snap.best_score_achieved, 0.8)
        self.assertEqual(snap.improvement_rate, 1.0)


class TestRepairScorer(unittest.TestCase):
    def test_scoring_formula(self):
        scorer = RepairScorer()
        # validation=0.9, confidence=0.8, risk=0.2 (contrib 0.8), maintainability=0.7, lines=2 (simplicity 1.0), new_bugs=0 (static 1.0)
        score = scorer.score(
            validation_score=0.9,
            candidate_confidence=0.8,
            estimated_risk=0.2,
            maintainability_score=0.7,
            lines_changed=2,
            new_bug_count=0,
        )
        # Expected:
        # val_comp = 0.40 * 0.9 = 0.36
        # conf_comp = 0.20 * 0.8 = 0.16
        # risk_comp = 0.15 * (1.0 - 0.2) = 0.12
        # maint_comp = 0.10 * 0.7 = 0.07
        # simp_comp = 0.10 * 1.0 = 0.10
        # static_comp = 0.05 * 1.0 = 0.05
        # Total = 0.36 + 0.16 + 0.12 + 0.07 + 0.10 + 0.05 = 0.86
        self.assertAlmostEqual(score.total, 0.86, places=3)
        self.assertTrue(scorer.passes_threshold(score))


class TestConvergenceDetector(unittest.TestCase):
    def test_convergence_absolute(self):
        detector = ConvergenceDetector(strategy=ConvergenceStrategy.ABSOLUTE_DELTA)
        # Delta defaults to 0.01, Window to 2
        # We need window+1 = 3 scores. Let's feed 0.5, 0.505, 0.508
        self.assertFalse(detector.check([0.5, 0.505]))
        self.assertTrue(detector.check([0.5, 0.505, 0.508]))


class TestTerminationPolicy(unittest.TestCase):
    def setUp(self):
        self.policy = TerminationPolicy()

    def test_termination_success(self):
        reason = self.policy.should_terminate(
            iteration_index=1,
            accepted=True,
            consecutive_failures=0,
            score_converged=False,
            candidate_pool_size=1,
        )
        self.assertEqual(reason, TerminationReason.ACCEPTED)

    def test_termination_max_iterations(self):
        reason = self.policy.should_terminate(
            iteration_index=5, # Default limit is 5
            accepted=False,
            consecutive_failures=0,
            score_converged=False,
            candidate_pool_size=2,
        )
        self.assertEqual(reason, TerminationReason.ITERATION_LIMIT)

    def test_termination_on_final_zero_based_iteration(self):
        """
        iteration_index is 0-based and checked after the iteration ran, so
        index 4 is the 5th completed pass and must stop at max_iterations=5.
        Comparing the raw index allowed a 6th iteration.
        """
        reason = self.policy.should_terminate(
            iteration_index=4,
            accepted=False,
            consecutive_failures=0,
            score_converged=False,
            candidate_pool_size=2,
        )
        self.assertEqual(reason, TerminationReason.ITERATION_LIMIT)

    def test_no_termination_before_limit(self):
        reason = self.policy.should_terminate(
            iteration_index=3,
            accepted=False,
            consecutive_failures=0,
            score_converged=False,
            candidate_pool_size=2,
        )
        self.assertIsNone(reason)

    def test_termination_convergence(self):
        reason = self.policy.should_terminate(
            iteration_index=2,
            accepted=False,
            consecutive_failures=0,
            score_converged=True,
            candidate_pool_size=2,
        )
        self.assertEqual(reason, TerminationReason.CONVERGENCE)


class TestFeedbackEngine(unittest.TestCase):
    def test_compilation_failure_feedback(self):
        engine = FeedbackEngine()
        report = ValidationReport(
            patch_id="p1",
            bug_id="b1",
            diagnostics=Diagnostics(errors=["missing include in file.cpp"]),
        )
        metrics = ValidationMetrics(
            compilation_success=False,
            syntax_success=True,
            bug_removal_rate=0.0,
            regression_success=True,
            score=0.0,
        )
        feedback = engine.extract(report, metrics, "c1", 0)
        self.assertTrue(feedback.compilation_failed)
        self.assertIn("Add the required #include", feedback.primary_suggestion)


class TestPlanningEngine(unittest.TestCase):
    def setUp(self):
        self.engine = PlanningEngine()

    def test_rule_based_generation(self):
        strategy = self.engine.decide_strategy(
            iteration_index=0,
            score_progression=[],
            feedback=None,
            planner_decision=None,
            strategies_tried=[],
            consecutive_failures=0,
        )
        self.assertEqual(strategy, RepairStrategy.GENERATE_NEW)

    def test_rule_based_refinement(self):
        strategy = self.engine.decide_strategy(
            iteration_index=1,
            score_progression=[0.5, 0.55],
            feedback=None,
            planner_decision=None,
            strategies_tried=["generate_new"],
            consecutive_failures=0,
        )
        self.assertEqual(strategy, RepairStrategy.REFINE_EXISTING)


class TestAgentManager(unittest.TestCase):
    def setUp(self):
        self.provider = MagicMock()
        self.cfg = RepairConfiguration()
        self.manager = AgentManager(self.provider, self.cfg)

    def test_get_and_cache(self):
        validator = self.manager.get_agent(AgentRole.VALIDATOR)
        self.assertIsNotNone(validator)
        # Check caching
        self.assertIs(self.manager.get_agent(AgentRole.VALIDATOR), validator)

    def test_disabled_agent(self):
        self.cfg.agents_enabled["planner"] = False
        with self.assertRaises(LookupError):
            self.manager.get_agent(AgentRole.PLANNER)

    def test_hot_swap(self):
        custom_agent = MagicMock()
        self.manager.register_agent(AgentRole.VALIDATOR, custom_agent)
        self.assertIs(self.manager.get_agent(AgentRole.VALIDATOR), custom_agent)


class TestPolicyRegistry(unittest.TestCase):
    def test_default_registry_configs(self):
        def_cfg = RepairPolicyRegistry.get_config("default")
        self.assertEqual(def_cfg.max_iterations, 5)

        cons_cfg = RepairPolicyRegistry.get_config("conservative")
        self.assertEqual(cons_cfg.max_iterations, 3)
        self.assertFalse(cons_cfg.agents_enabled["planner"])

        agg_cfg = RepairPolicyRegistry.get_config("aggressive")
        self.assertEqual(agg_cfg.max_iterations, 8)
        self.assertEqual(agg_cfg.candidates_per_iteration, 3)


class TestRefinementEngine(unittest.IsolatedAsyncioTestCase):
    async def test_refinement_calls_llm(self):
        provider = AsyncMock()
        provider.generate_completion_async.return_value = json.dumps({
            "patched_code": "int x = 42;",
            "reasoning": "refined fix",
            "confidence": 0.9,
        })
        
        parent = PatchCandidate(
            candidate_id="c_parent",
            original_code="int x = 5;",
            patched_code="int x = 10;",
        )
        feedback = StructuredFeedback(
            candidate_id="c_parent",
            iteration=0,
            failure_reasons=[FailureReason(severity=FailureSeverity.HIGH, category="api", description="wrong API")],
        )

        engine = RefinementEngine(provider)
        refined = await engine.refine(parent, feedback, RefinementStrategy.ALTERNATIVE_API)
        self.assertEqual(refined.patched_code, "int x = 42;")
        self.assertEqual(refined.reasoning, "refined fix")


class TestReasoningEngine(unittest.IsolatedAsyncioTestCase):
    async def test_reasoning_returns_decision(self):
        provider = AsyncMock()
        provider.generate_completion_async.return_value = json.dumps({
            "strategy": "refine_existing",
            "reasoning": "failed compilation due to missing include",
            "preserve_elements": ["main()"],
            "modify_elements": ["include section"],
            "recommended_actions": ["Add include"],
            "confidence": 0.8,
        })

        feedback = StructuredFeedback(
            candidate_id="c1",
            iteration=0,
            failure_reasons=[FailureReason(severity=FailureSeverity.CRITICAL, category="compilation", description="missing file.h")],
        )

        engine = ReasoningEngine(provider)
        decision = await engine.reason_about_failure(feedback, patch=None, report=None)
        self.assertEqual(decision.strategy, RepairStrategy.REFINE_EXISTING)
        self.assertIn("main()", decision.preserve_elements)


class TestRepairLoopAndOrchestrator(unittest.IsolatedAsyncioTestCase):
    @patch("backend.core.autonomous_repair.repair_loop.ValidationEngine")
    async def test_successful_repair_loop(self, mock_val_engine_class):
        # 1. Setup mock validation engine
        mock_val_engine = MagicMock()
        mock_val_engine_class.return_value = mock_val_engine
        
        # Validation output passes acceptance threshold
        metrics_pass = ValidationMetrics(
            compilation_success=True,
            syntax_success=True,
            bug_removal_rate=1.0,
            regression_success=True,
            score=0.9,
        )
        
        mock_val_report = ValidationReport(
            patch_id="p1",
            bug_id="BUG-001",
            accepted=True,
            winner_candidate_id="cand_1",
            metrics={"cand_1": metrics_pass},
        )
        mock_val_engine.validate_patch = AsyncMock(return_value=mock_val_report)

        # 2. Setup mock LLM provider
        provider = AsyncMock()
        # Mocking PlannerAgent response
        provider.generate_completion_async.return_value = json.dumps({
            "strategy": "refine_existing",
            "reasoning": "looking good",
            "priority_actions": ["Validate"],
            "confidence": 0.9,
        })

        # 3. Setup mock finding
        finding = MagicMock()
        finding.rule_id = "NULL_PTR"
        finding.description = "Possible null pointer dereference"
        finding.file_path = "src/main.cpp"
        finding.line_number = 42

        # 4. Setup mock patch candidate generated by generator agent
        candidate = PatchCandidate(
            candidate_id="cand_1",
            original_code="int* p = nullptr; *p = 5;",
            patched_code="int* p = nullptr; if (p) *p = 5;",
            confidence=0.8,
        )
        file_patch = FilePatch(file_path="src/main.cpp", candidates=[candidate])
        structured_patch = StructuredPatch(bug_id="BUG-001", file_patches=[file_patch])

        # Instantiate orchestrator and override the patch generator's engine
        orchestrator = RepairOrchestrator(provider)
        
        with patch("backend.core.autonomous_repair.agents.patch_generator_agent.PatchGenerationEngine") as mock_gen_engine_class:
            mock_gen_engine = AsyncMock()
            mock_gen_engine.generate.return_value = structured_patch
            mock_gen_engine_class.return_value = mock_gen_engine

            session = await orchestrator.run(
                finding=finding,
                code="int* p = nullptr; *p = 5;",
                bug_id="BUG-001",
            )

            self.assertTrue(session.accepted)
            self.assertEqual(session.termination_reason, TerminationReason.ACCEPTED)
            self.assertEqual(len(session.iterations), 1)
            self.assertIsNotNone(session.report)
            self.assertIsNotNone(session.audit_trail_jsonl)


class TestRepairLoopNoCandidates(unittest.IsolatedAsyncioTestCase):
    """
    A generator that never produces candidates must still terminate.

    The no-candidate branch used to `continue` straight to the next iteration,
    skipping the termination policy -- and the wall-clock timeout lives inside
    that same check. A persistently failing generator (missing API key,
    retired model, provider outage) therefore looped forever.

    Each test is wrapped in asyncio.wait_for so a regression surfaces as a
    timeout failure rather than hanging the suite.
    """

    @staticmethod
    def _finding():
        finding = MagicMock()
        finding.rule_id = "NULL_PTR"
        finding.description = "Possible null pointer dereference"
        finding.file_path = "src/main.cpp"
        finding.line_number = 42
        return finding

    @staticmethod
    def _orchestrator(**cfg_kw):
        provider = AsyncMock()
        provider.generate_completion_async.return_value = None
        return RepairOrchestrator(provider, RepairConfiguration(**cfg_kw))

    @patch("backend.core.autonomous_repair.repair_loop.ValidationEngine")
    async def test_terminates_when_nothing_is_ever_generated(self, mock_val_engine_class):
        """Pool never fills, so the loop stops with NO_CANDIDATES."""
        mock_val_engine_class.return_value = MagicMock()
        orchestrator = self._orchestrator(max_iterations=5, max_consecutive_failures=99)

        with patch(
            "backend.core.autonomous_repair.agents.patch_generator_agent.PatchGenerationEngine"
        ) as mock_gen_engine_class:
            mock_gen_engine = AsyncMock()
            mock_gen_engine.generate.side_effect = RuntimeError("provider unavailable")
            mock_gen_engine_class.return_value = mock_gen_engine

            session = await asyncio.wait_for(
                orchestrator.run(finding=self._finding(), code="int x;", bug_id="BUG-X"),
                timeout=30,
            )

        self.assertFalse(session.accepted)
        self.assertEqual(session.termination_reason, TerminationReason.NO_CANDIDATES)
        # Must stop immediately rather than burning every iteration.
        self.assertEqual(len(session.iterations), 1)
        self.assertFalse(session.iterations[0].improved)
        self.assertIn("iteration_end", session.audit_trail_jsonl)

    @patch("backend.core.autonomous_repair.repair_loop.ValidationEngine")
    async def test_terminates_when_generation_fails_after_a_success(self, mock_val_engine_class):
        """
        The harder path: the pool is non-empty, so NO_CANDIDATES does not
        apply and the failed iterations must fall through to the iteration
        limit instead of looping forever.
        """
        mock_val_engine = MagicMock()
        mock_val_engine_class.return_value = mock_val_engine
        # Scores below the acceptance threshold so the loop keeps going.
        mock_val_engine.validate_patch = AsyncMock(return_value=ValidationReport(
            patch_id="p1",
            bug_id="BUG-X",
            accepted=False,
            metrics={"cand_1": ValidationMetrics(
                compilation_success=True,
                syntax_success=True,
                bug_removal_rate=0.0,
                regression_success=False,
                score=0.1,
            )},
        ))

        candidate = PatchCandidate(
            candidate_id="cand_1",
            original_code="int* p = nullptr; *p = 5;",
            patched_code="int* p = nullptr; if (p) *p = 5;",
            confidence=0.4,
        )
        good_patch = StructuredPatch(
            bug_id="BUG-X",
            file_patches=[FilePatch(file_path="src/main.cpp", candidates=[candidate])],
        )

        orchestrator = self._orchestrator(max_iterations=3, max_consecutive_failures=99)

        with patch(
            "backend.core.autonomous_repair.agents.patch_generator_agent.PatchGenerationEngine"
        ) as mock_gen_engine_class:
            mock_gen_engine = AsyncMock()
            # Succeed once, then fail forever.
            mock_gen_engine.generate.side_effect = [
                good_patch,
                RuntimeError("provider unavailable"),
                RuntimeError("provider unavailable"),
                RuntimeError("provider unavailable"),
                RuntimeError("provider unavailable"),
            ]
            mock_gen_engine_class.return_value = mock_gen_engine

            session = await asyncio.wait_for(
                orchestrator.run(finding=self._finding(), code="int x;", bug_id="BUG-X"),
                timeout=30,
            )

        self.assertFalse(session.accepted)
        self.assertIn(
            session.termination_reason,
            (TerminationReason.ITERATION_LIMIT, TerminationReason.CONVERGENCE),
        )
        self.assertLessEqual(len(session.iterations), 3)


if __name__ == "__main__":
    unittest.main()
