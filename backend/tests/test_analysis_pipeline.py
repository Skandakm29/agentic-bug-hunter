"""
Phase 3A / 3C.2 Analysis Pipeline Regression Suite
===================================================
Covers the detection pipeline end-to-end plus the program-graph builders and
reasoning engines it depends on.

This suite exists because the Phase 3C.2 façade previously had no coverage at
all: ``DetectionOrchestrator.run()`` raised ``TypeError`` on the very first
finding and nothing caught it. Every test here pins behaviour that was either
broken or unverified.
"""
import sys
import unittest
from pathlib import Path

# Inject project root so imports resolve correctly
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.core.analysis.detection_orchestrator import DetectionOrchestrator
from backend.core.analysis.dfg import DFGConstructor
from backend.core.analysis.engine import AnalysisEngine
from backend.core.analysis.ipa import IPATracer
from backend.core.analysis.patch import PatchPlanningEngine
from backend.core.analysis.regression import RegressionAnalyzer, RegressionImpactReport
from backend.core.analysis.remediation import RemediationReasoner
from backend.core.analysis.repo_graph import RepositoryKnowledgeGraph
from backend.core.analysis.root_cause import RootCauseAnalyzer
from backend.core.analysis.schemas import NormalizedFinding
from backend.core.ai.memory.semantic import SemanticMemory


SAMPLE = """void helper(int v) {
    int scaled = v * 2;
    rdi.frobnicate(scaled);
}

void IRAM_ATTR my_isr() {
    delay(100);
    uint8_t total = 0;
    bool flag = 1;
    helper(total);
}
"""


def _finding(**kw) -> NormalizedFinding:
    base = dict(
        rule_id="null_pointer",
        file_path="src/driver.cpp",
        line_number=3,
        line_text="*p = 5;",
        severity="HIGH",
        static_confidence=0.9,
        description="Pointer dereferenced without allocation.",
        evidence="Dereference: *p",
        remediation="Allocate before dereferencing.",
    )
    base.update(kw)
    return NormalizedFinding(**base)


# ---------------------------------------------------------------------------
# Data Flow Graph
# ---------------------------------------------------------------------------


class TestDFGConstructor(unittest.TestCase):
    def setUp(self):
        self.dfg = DFGConstructor()

    def _deps(self, code):
        return [
            (d.source_var, d.target_var)
            for d in self.dfg.construct(code.splitlines()).dependencies
        ]

    def test_plain_assignment(self):
        self.assertIn(("y", "x"), self._deps("x = y + 1;"))

    def test_typed_declaration_is_captured(self):
        """Declarations with a type are the common case and were all missed."""
        self.assertIn(("v", "scaled"), self._deps("int scaled = v * 2;"))

    def test_qualified_template_declaration(self):
        self.assertIn(("src", "buf"), self._deps("std::vector<int> buf = src;"))

    def test_pointer_declaration(self):
        self.assertIn(("q", "p"), self._deps("int *p = q;"))

    def test_compound_assignment(self):
        self.assertIn(("delta", "total"), self._deps("total += delta;"))

    def test_equality_comparison_is_not_an_assignment(self):
        self.assertEqual(self._deps("if (total == 5) { }"), [])

    def test_other_comparisons_are_not_assignments(self):
        for op in ("!=", "<=", ">="):
            with self.subTest(op=op):
                self.assertEqual(self._deps(f"if (a {op} b) {{ }}"), [])

    def test_keywords_are_not_sources(self):
        deps = self._deps("int x = true;")
        self.assertNotIn(("true", "x"), deps)

    def test_self_reference_excluded(self):
        self.assertNotIn(("total", "total"), self._deps("total = total + 1;"))


# ---------------------------------------------------------------------------
# Interprocedural call graph
# ---------------------------------------------------------------------------


class TestIPATracer(unittest.TestCase):
    def setUp(self):
        self.ipa = IPATracer()

    def _edges(self, code):
        return [(e.caller, e.callee) for e in self.ipa.trace_calls(code).edges]

    def test_simple_function_scope(self):
        edges = self._edges("void f() {\n    g();\n}\n")
        self.assertIn(("f", "g"), edges)

    def test_attribute_macro_in_declaration(self):
        """`void IRAM_ATTR my_isr() {` must register as a declaration."""
        edges = self._edges("void IRAM_ATTR my_isr() {\n    delay(10);\n}\n")
        self.assertIn(("my_isr", "delay"), edges)
        self.assertNotIn(("global", "my_isr"), edges)

    def test_nested_blocks_preserve_scope(self):
        code = "void f() {\n    if (x) {\n        g();\n    }\n    h();\n}\n"
        edges = self._edges(code)
        self.assertIn(("f", "g"), edges)
        self.assertIn(("f", "h"), edges)

    def test_scope_resets_after_function_body(self):
        code = "void f() {\n    g();\n}\n\nvoid k() {\n    m();\n}\n"
        edges = self._edges(code)
        self.assertIn(("f", "g"), edges)
        self.assertIn(("k", "m"), edges)
        self.assertNotIn(("f", "m"), edges)

    def test_control_keywords_are_not_calls(self):
        edges = self._edges("void f() {\n    if (a) { while (b) { c(); } }\n}\n")
        callees = [c for _, c in edges]
        self.assertNotIn("if", callees)
        self.assertNotIn("while", callees)
        self.assertIn("c", callees)


# ---------------------------------------------------------------------------
# Root cause
# ---------------------------------------------------------------------------


class TestRootCauseAnalyzer(unittest.TestCase):
    def test_dfg_and_ipa_hypotheses_fire_when_graphs_supplied(self):
        graph = RepositoryKnowledgeGraph()
        orch = DetectionOrchestrator(repo_graph=graph)
        orch._seed_repo_graph(SAMPLE, IPATracer().trace_calls(SAMPLE), "src/driver.cpp")

        dfg_deps = DFGConstructor().construct(SAMPLE.splitlines()).dependencies
        chain = RootCauseAnalyzer().analyze(
            _finding(line_text="int scaled = v * 2;"),
            graph,
            dfg_deps=dfg_deps,
            call_graph=IPATracer().trace_calls(SAMPLE),
        )
        hypotheses = " ".join(n.hypothesis for n in chain.nodes)
        self.assertGreater(len(chain.nodes), 1)
        self.assertIn("scaled", hypotheses)          # H2 — DFG slice
        self.assertIn("my_isr", hypotheses)          # H3 — IPA caller

    def test_only_h1_without_graphs(self):
        chain = RootCauseAnalyzer().analyze(_finding(), RepositoryKnowledgeGraph())
        self.assertEqual(len(chain.nodes), 1)


# ---------------------------------------------------------------------------
# Remediation
# ---------------------------------------------------------------------------


class TestRemediationReasoner(unittest.TestCase):
    def _plan(self):
        graph = RepositoryKnowledgeGraph()
        f = _finding()
        rc = RootCauseAnalyzer().analyze(f, graph)
        return PatchPlanningEngine().plan(f, rc, graph, [])

    def test_empty_semantic_memory_still_yields_viable_strategies(self):
        """
        An unpopulated SemanticMemory means "no evidence", not "zero score".
        Treating it as zero capped correctness at 0.40 x static, which could
        never clear the 0.40 floor, so every plan returned NO_VIABLE_STRATEGY.
        """
        result = RemediationReasoner(semantic_memory=SemanticMemory()).evaluate(self._plan())
        self.assertFalse(result.no_viable)
        self.assertGreater(len(result.accepted), 0)

    def test_agent_consensus_is_weighted_when_supplied(self):
        with_consensus = RemediationReasoner(
            semantic_memory=SemanticMemory(), agent_consensus_score=1.0
        ).evaluate(self._plan())
        without = RemediationReasoner(semantic_memory=SemanticMemory()).evaluate(self._plan())
        self.assertGreater(
            with_consensus.best.correctness_estimate,
            without.best.correctness_estimate,
        )

    def test_low_correctness_still_rejected(self):
        """The floor must still reject genuinely weak strategies."""
        plan = self._plan()
        for s in plan.strategies:
            s.estimated_correctness = 0.05
        result = RemediationReasoner(semantic_memory=SemanticMemory()).evaluate(plan)
        self.assertTrue(result.no_viable)


# ---------------------------------------------------------------------------
# Regression analyzer
# ---------------------------------------------------------------------------


class TestRegressionAnalyzer(unittest.TestCase):
    def test_report_constructs_without_modified_spans(self):
        """modified_spans is populated by analyze(), so it needs a default."""
        report = RegressionImpactReport(finding_rule_id="r1")
        self.assertEqual(report.modified_spans, [])

    def test_analyze_returns_report(self):
        graph = RepositoryKnowledgeGraph()
        f = _finding()
        rc = RootCauseAnalyzer().analyze(f, graph)
        plan = PatchPlanningEngine().plan(f, rc, graph, [])
        rp = RemediationReasoner(semantic_memory=SemanticMemory()).evaluate(plan)
        report = RegressionAnalyzer().analyze(rp, graph)
        self.assertEqual(report.finding_rule_id, f.rule_id)
        self.assertIn(report.api_compat_verdict, ("SAFE", "AT_RISK", "UNKNOWN"))


# ---------------------------------------------------------------------------
# AnalysisEngine
# ---------------------------------------------------------------------------


class TestAnalysisEngine(unittest.TestCase):
    def test_file_path_is_stamped_onto_findings(self):
        findings = AnalysisEngine().analyze_plain(SAMPLE, "cpp", file_path="src/driver.cpp")
        self.assertTrue(findings)
        for f in findings:
            self.assertEqual(f.file_path, "src/driver.cpp")

    def test_file_path_optional(self):
        findings = AnalysisEngine().analyze_plain(SAMPLE)
        self.assertTrue(findings)
        self.assertIsNone(findings[0].file_path)

    def test_repeated_rule_keeps_per_occurrence_enrichment(self):
        """
        Enrichments were keyed by rule_id, so a rule firing twice in one file
        left both findings sharing (and one overwriting) the other's evidence
        graph, confidence result, and explanation.
        """
        code = (
            "void f() {\n"
            "    uint8_t total = 0;\n"
            "    uint8_t count = 0;\n"
            "}\n"
        )
        enriched = AnalysisEngine().analyze(code, "cpp", file_path="a.cpp")
        overflow = [e for e in enriched if e.finding.rule_id == "overflow_risk"]
        self.assertEqual(len(overflow), 2, "expected the rule to fire on both lines")

        lines = {e.finding.line_number for e in overflow}
        self.assertEqual(lines, {2, 3})

        # Each occurrence carries its own distinct evidence graph.
        graph_ids = {e.evidence_graph.graph_id for e in overflow}
        self.assertEqual(len(graph_ids), 2)
        for e in overflow:
            self.assertIsNotNone(e.confidence_result)
            self.assertIsNotNone(e.explanation)


# ---------------------------------------------------------------------------
# DetectionOrchestrator — end to end
# ---------------------------------------------------------------------------


class TestDetectionOrchestrator(unittest.TestCase):
    def setUp(self):
        self.result = DetectionOrchestrator().run(
            code=SAMPLE, extension="cpp", file_path="src/driver.cpp"
        )

    def test_run_completes(self):
        self.assertGreater(len(self.result.enriched_findings), 0)
        self.assertEqual(
            len(self.result.finding_analyses), len(self.result.enriched_findings)
        )

    def test_all_four_report_formats_rendered(self):
        for blob in (
            self.result.json_report,
            self.result.sarif_report,
            self.result.markdown_report,
            self.result.html_report,
        ):
            self.assertIsInstance(blob, bytes)
            self.assertGreater(len(blob), 0)

    def test_sarif_is_valid_json_with_locations(self):
        import json
        doc = json.loads(self.result.sarif_report)
        self.assertEqual(doc["version"], "2.1.0")
        results = doc["runs"][0]["results"]
        self.assertTrue(results)
        uri = results[0]["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
        self.assertEqual(uri, "src/driver.cpp")

    def test_localizer_resolves_enclosing_function(self):
        spans = self.result.finding_analyses[0].spans
        self.assertTrue(spans)
        self.assertTrue(
            any(s.symbol_name for s in spans),
            "repo graph seeding should let the localizer name the enclosing symbol",
        )

    def test_remediation_produces_viable_strategies(self):
        for fa in self.result.finding_analyses:
            self.assertFalse(fa.remediation.no_viable)

    def test_root_cause_uses_program_graphs(self):
        multi = [fa for fa in self.result.finding_analyses if len(fa.root_cause.nodes) > 1]
        self.assertTrue(multi, "DFG/IPA hypotheses should be reachable")

    def test_cross_file_trace_attached(self):
        self.assertTrue(
            any(fa.cross_file is not None for fa in self.result.finding_analyses)
        )

    def test_runs_inside_a_running_event_loop(self):
        """run() is sync but is called from async FastAPI handlers."""
        import asyncio

        async def main():
            return DetectionOrchestrator().run(
                code=SAMPLE, extension="cpp", file_path="src/driver.cpp"
            )

        result = asyncio.run(main())
        self.assertGreater(len(result.enriched_findings), 0)


if __name__ == "__main__":
    unittest.main()
