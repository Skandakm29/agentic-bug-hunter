"""
API / Integration Contract Regression Suite
============================================
Pins the seams between subsystems that pass plain dicts rather than typed
models. Every case here previously silently degraded or crashed because the
two sides of the seam disagreed on a field name or a value's type.
"""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

# Inject project root so imports resolve correctly
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.core.ai.agents.report_generator import ReportGeneratorAgent
from backend.core.ai.agents.validator import ValidatorAgent
from backend.core.ai.router import ModelRoutingEngine
from backend.core.ai.schemas import BugValidationResult, CodeCorrection
from backend.core.analysis.schemas import NormalizedFinding
from backend.core.config import settings


def _finding_dict(**kw) -> dict:
    f = NormalizedFinding(
        rule_id="blocking_in_isr",
        line_number=7,
        line_text="delay(100);",
        severity="HIGH",
        static_confidence=0.85,
        description="Blocking call inside interrupt context.",
        evidence="delay(100);",
        remediation="Use a hardware timer.",
    )
    d = f.model_dump()
    d.update(kw)
    return d


# ---------------------------------------------------------------------------
# rule_id -> rule_tag
# ---------------------------------------------------------------------------


class TestRuleIdentifierMapping(unittest.TestCase):
    """
    NormalizedFinding serialises the identifier as `rule_id`, but the client
    contract (FindingDetail and the Next.js UI) calls it `rule_tag`. Reading
    only `rule_tag` made every finding render as "unknown" in the UI and put
    "Rule: unknown" in every validator prompt.
    """

    def test_report_agent_maps_rule_id_to_rule_tag(self):
        agent = ReportGeneratorAgent(provider=None)
        report = agent.compile_report(
            findings=[_finding_dict()],
            validation_result=BugValidationResult(
                valid_bug=True, explanation="real bug", confidence=0.9
            ),
            correction=CodeCorrection(corrected_code="// fixed"),
            combined_confidence=0.87,
        )
        self.assertEqual(report["static_findings"][0]["rule_tag"], "blocking_in_isr")

    def test_report_agent_still_accepts_legacy_rule_tag(self):
        agent = ReportGeneratorAgent(provider=None)
        legacy = {
            "line_number": 1,
            "line_text": "x;",
            "rule_tag": "legacy_rule",
            "description": "d",
            "static_confidence": 0.5,
        }
        report = agent.compile_report(
            findings=[legacy],
            validation_result=None,
            correction=None,
            combined_confidence=0.0,
        )
        self.assertEqual(report["static_findings"][0]["rule_tag"], "legacy_rule")

    def test_report_agent_falls_back_to_unknown(self):
        agent = ReportGeneratorAgent(provider=None)
        report = agent.compile_report(
            findings=[{"line_text": "x;", "description": "d"}],
            validation_result=None,
            correction=None,
            combined_confidence=0.0,
        )
        self.assertEqual(report["static_findings"][0]["rule_tag"], "unknown")

    def test_validator_prompt_contains_real_rule_id(self):
        provider = AsyncMock()
        provider.generate_completion_async.return_value = (
            '{"valid_bug": true, "explanation": "e", "confidence": 0.8}'
        )
        agent = ValidatorAgent(provider)

        asyncio.run(agent.validate_finding("delay(100);", _finding_dict()))

        prompt = provider.generate_completion_async.call_args.kwargs["prompt"]
        self.assertIn("blocking_in_isr", prompt)
        self.assertNotIn("Rule: unknown", prompt)


# ---------------------------------------------------------------------------
# MCP batch tool
# ---------------------------------------------------------------------------


class TestMCPBatchAnalyze(unittest.TestCase):
    """
    orchestrate_async returns AnalysisReport.dict(), so static_findings holds
    plain dicts. Attribute access (f.rule_id) raised AttributeError on the
    first CSV row with any finding.
    """

    def test_batch_analyze_handles_dict_findings(self):
        import csv
        import tempfile
        from backend.core.mcp import tools

        fake_report = {
            "static_findings": [
                {"rule_tag": "blocking_in_isr", "line_text": "delay(100);"},
                {"rule_id": "overflow_risk", "line_text": "uint8_t t;"},
            ],
            "llm_result": {"valid_bug": True, "confidence": 0.9},
            "total_issues": 2,
            "llm_available": True,
        }

        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "samples.csv"
            with open(csv_path, "w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=["code"])
                writer.writeheader()
                writer.writerow({"code": "void f(){ delay(1); }"})

            with patch.object(
                tools, "orchestrate_async", AsyncMock(return_value=fake_report)
            ):
                result = asyncio.run(tools.mcp_batch_analyze(str(csv_path)))

        self.assertNotIn("error", result)
        self.assertEqual(result["total_samples"], 1)
        self.assertEqual(result["bugs_found"], 1)
        self.assertEqual(
            result["results"][0]["rule_tags"], ["blocking_in_isr", "overflow_risk"]
        )
        self.assertTrue(result["results"][0]["llm_valid_bug"])

    def test_batch_analyze_missing_file(self):
        from backend.core.mcp import tools
        result = asyncio.run(tools.mcp_batch_analyze("does_not_exist.csv"))
        self.assertIn("error", result)


# ---------------------------------------------------------------------------
# Model configuration
# ---------------------------------------------------------------------------


class TestModelRouting(unittest.TestCase):
    """
    Every hard-coded Groq model ID in the original code had been retired, so
    the whole fallback chain failed and every LLM feature silently returned
    None. IDs now come from Settings and are environment-overridable.
    """

    def test_router_sources_models_from_settings(self):
        routes = ModelRoutingEngine().routes
        self.assertEqual(routes["classification"]["model"], settings.GROQ_MODEL)
        self.assertEqual(routes["static_validation"]["model"], settings.GROQ_MODEL)
        self.assertEqual(routes["complex_fix"]["model"], settings.GROQ_REASONING_MODEL)

    def test_no_retired_model_ids_are_referenced(self):
        retired = {
            "llama3-8b-8192",
            "llama-3.1-70b-versatile",
            "mixtral-8x7b-32768",
            "llama3-70b-8192",
        }
        configured = {
            settings.GROQ_MODEL,
            settings.GROQ_REASONING_MODEL,
            *settings.GROQ_FALLBACK_MODELS,
        }
        self.assertEqual(configured & retired, set())

    def test_unknown_task_falls_back_to_static_validation(self):
        route = ModelRoutingEngine().route_task("no_such_task")
        self.assertEqual(route["model"], settings.GROQ_MODEL)

    def test_fallback_models_are_configured(self):
        self.assertTrue(settings.GROQ_FALLBACK_MODELS)
        for model in settings.GROQ_FALLBACK_MODELS:
            self.assertIsInstance(model, str)
            self.assertTrue(model.strip())


class TestDocumentedExtensionPoints(unittest.TestCase):
    """
    Every extension point the README tells users to call must exist and work
    exactly as documented. Three of these previously did not.
    """

    def test_rule_registry_decorator_form(self):
        from backend.core.analysis.rules.base import BaseRule
        from backend.core.analysis.rules.registry import RuleRegistry

        @RuleRegistry.register
        class _DocRule(BaseRule):
            rule_id = "doc_example_rule"
            name = "Doc Example"
            description = "d"
            category = "c"
            language = "cpp"
            severity = "HIGH"
            confidence = 0.85

            def execute(self, representation):
                return []

        # The decorator must return the class, not None.
        self.assertTrue(callable(_DocRule))
        self.assertEqual(_DocRule.rule_id, "doc_example_rule")
        registered = [r.rule_id for r in RuleRegistry.get_rules_for_language("cpp")]
        self.assertIn("doc_example_rule", registered)

        # Keep the shared registry clean for other tests.
        RuleRegistry._rules["cpp"] = [
            r for r in RuleRegistry._rules["cpp"] if r is not _DocRule
        ]

    def test_parser_registry_register(self):
        from backend.core.analysis.parsers.base import BaseParser, CodeRepresentation
        from backend.core.analysis.parsers.registry import ParserRegistry

        class _DocParser(BaseParser):
            def parse(self, code: str) -> CodeRepresentation:
                return CodeRepresentation(lines=[], raw_code=code, metadata={})

        original = dict(ParserRegistry._parsers)
        try:
            ParserRegistry.register("docext", _DocParser)
            self.assertIsInstance(ParserRegistry.get_parser("docext"), _DocParser)
            self.assertIsInstance(ParserRegistry.get_parser(".docext"), _DocParser)
            self.assertIn("docext", ParserRegistry.supported_extensions())
        finally:
            ParserRegistry._parsers = original

    def test_compiler_registry_register(self):
        from backend.core.patch_validation.compiler import BaseCompiler, GCCCompiler
        from backend.core.patch_validation.compiler_registry import CompilerRegistry

        class _DocCompiler(BaseCompiler):
            async def compile(self, workspace_path, source_files,
                              build_command=None, timeout=30):
                raise NotImplementedError

        original = dict(CompilerRegistry._compilers)
        try:
            CompilerRegistry.register("intel", _DocCompiler)
            self.assertIsInstance(CompilerRegistry.get_compiler("intel"), _DocCompiler)
            self.assertIn("intel", CompilerRegistry.available())
            # Unknown names still fall back to GCC.
            self.assertIsInstance(CompilerRegistry.get_compiler("nope"), GCCCompiler)
        finally:
            CompilerRegistry._compilers = original

    def test_build_system_registry_register(self):
        from backend.core.patch_validation.build_system import (
            BaseBuildSystem,
            BuildSystemRegistry,
            NoneBuildSystem,
        )

        class _DocBuildSystem(BaseBuildSystem):
            async def configure(self, workspace_path, compiler_type, parallel_jobs=4):
                return True

            async def build(self, workspace_path, compiler_type,
                            parallel_jobs=4, timeout=60):
                raise NotImplementedError

        original = dict(BuildSystemRegistry._systems)
        try:
            BuildSystemRegistry.register("meson", _DocBuildSystem)
            self.assertIsInstance(
                BuildSystemRegistry.get_build_system("meson"), _DocBuildSystem
            )
            self.assertIn("meson", BuildSystemRegistry.available())
            self.assertIsInstance(
                BuildSystemRegistry.get_build_system("nope"), NoneBuildSystem
            )
        finally:
            BuildSystemRegistry._systems = original

    def test_suppression_filter_registration(self):
        from backend.core.analysis.suppression import (
            BaseSuppressionFilter,
            SuppressionPipeline,
        )

        class _TeamFilter(BaseSuppressionFilter):
            name = "TeamFilter"

            def apply(self, findings):
                return [], [
                    __import__(
                        "backend.core.analysis.suppression",
                        fromlist=["SuppressedFindingRecord"],
                    ).SuppressedFindingRecord(f, self.name, "team policy")
                    for f in findings
                ]

        pipeline = SuppressionPipeline.default()
        self.assertIs(pipeline.register_filter(_TeamFilter()), pipeline)
        result = pipeline.run([_finding_model()])
        self.assertEqual(result.passed, [])
        self.assertTrue(any(r.suppressor == "TeamFilter" for r in result.suppressed))


def _finding_model() -> NormalizedFinding:
    return NormalizedFinding(
        rule_id="blocking_in_isr",
        line_number=7,
        line_text="delay(100);",
        severity="HIGH",
        static_confidence=0.85,
        description="Blocking call inside interrupt context.",
        evidence="delay(100);",
        remediation="Use a hardware timer.",
    )


class TestSettingsEnvOverride(unittest.TestCase):
    def test_models_are_environment_overridable(self):
        import importlib
        import os
        from backend.core import config as config_module

        env = {
            "GROQ_MODEL": "custom-fast",
            "GROQ_REASONING_MODEL": "custom-big",
            "GROQ_FALLBACK_MODELS": "fb-one, fb-two ,",
        }
        with patch.dict(os.environ, env):
            reloaded = importlib.reload(config_module)
            try:
                self.assertEqual(reloaded.settings.GROQ_MODEL, "custom-fast")
                self.assertEqual(reloaded.settings.GROQ_REASONING_MODEL, "custom-big")
                self.assertEqual(
                    reloaded.settings.GROQ_FALLBACK_MODELS, ["fb-one", "fb-two"]
                )
            finally:
                # Restore module-level state for the rest of the suite.
                importlib.reload(config_module)


if __name__ == "__main__":
    unittest.main()
