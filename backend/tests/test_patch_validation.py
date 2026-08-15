import sys
from pathlib import Path
# Inject the project root so imports resolve correctly
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import os
import unittest
import tempfile
import shutil
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from backend.core.patch_generation.patch_models import StructuredPatch, FilePatch, PatchCandidate
from backend.core.patch_validation.configuration import PatchValidationConfig
from backend.core.patch_validation.validation_models import Workspace, CompilationResult, RegressionResult, StaticReanalysisResult, ValidationReport, ValidationMetrics, CandidateRanking, Diagnostics
from backend.core.patch_validation.exceptions import PatchValidationError, WorkspaceError, PatchApplyError, SyntaxError
from backend.core.patch_validation.workspace_manager import WorkspaceManager
from backend.core.patch_validation.patch_applier import PatchApplier
from backend.core.patch_validation.syntax_validator import SyntaxValidator
from backend.core.patch_validation.compiler import GCCCompiler, ClangCompiler, MSVCCompiler
from backend.core.patch_validation.compiler_registry import CompilerRegistry
from backend.core.patch_validation.build_system import BuildSystemRegistry, CMakeBuildSystem, MakeBuildSystem, NoneBuildSystem
from backend.core.patch_validation.static_validator import StaticValidator
from backend.core.patch_validation.test_discovery import TestDiscovery
from backend.core.patch_validation.regression_runner import RegressionRunner
from backend.core.patch_validation.quality_metrics import QualityMetricsCalculator
from backend.core.patch_validation.diagnostics import DiagnosticsCollector
from backend.core.patch_validation.rollback import RollbackManager
from backend.core.patch_validation.candidate_ranker import CandidateRanker
from backend.core.patch_validation.validation_report import ValidationReportGenerator
from backend.core.patch_validation.validation_engine import ValidationEngine


class TestWorkspaceManager(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.src_dir = os.path.join(self.temp_dir.name, "src")
        os.makedirs(self.src_dir, exist_ok=True)
        self.file_path = os.path.join(self.src_dir, "main.cpp")
        with open(self.file_path, "w") as f:
            f.write("#include <iostream>\nint main() { return 0; }\n")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_temp_dir_workspace(self):
        with WorkspaceManager(self.src_dir, "temp_dir") as ws:
            self.assertTrue(os.path.exists(ws.path))
            self.assertTrue(os.path.exists(os.path.join(ws.path, "main.cpp")))
            self.assertEqual(ws.original_path, self.src_dir)
        # Verify it cleaned up
        self.assertFalse(os.path.exists(ws.path))

    def test_none_workspace_type(self):
        with WorkspaceManager(self.src_dir, "none") as ws:
            self.assertEqual(ws.path, self.src_dir)


class TestPatchApplier(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.file_path = os.path.join(self.temp_dir.name, "main.cpp")
        self.original_code = """#include <iostream>
int main() {
    int x = 5;
    std::cout << x << std::endl;
    return 0;
}
"""
        with open(self.file_path, "w") as f:
            f.write(self.original_code)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_apply_patch_block_replace(self):
        candidate = MagicMock()
        candidate.unified_diff = ""
        candidate.original_code = "    int x = 5;"
        candidate.patched_code = "    int x = 42;"
        
        applier = PatchApplier()
        applier.apply_patch(self.temp_dir.name, "main.cpp", candidate)
        
        with open(self.file_path, "r") as f:
            content = f.read()
        self.assertIn("int x = 42;", content)
        self.assertNotIn("int x = 5;", content)

    def test_apply_patch_unified_diff(self):
        candidate = MagicMock()
        candidate.unified_diff = """@@ -3,2 +3,2 @@
-    int x = 5;
-    std::cout << x << std::endl;
+    int x = 100;
+    std::cout << "value is " << x << std::endl;
"""
        candidate.original_code = ""
        candidate.patched_code = ""

        applier = PatchApplier()
        applier.apply_patch(self.temp_dir.name, "main.cpp", candidate)

        with open(self.file_path, "r") as f:
            content = f.read()
        self.assertIn("int x = 100;", content)
        self.assertNotIn("int x = 5;", content)


class TestSyntaxValidator(unittest.TestCase):

    def test_valid_code(self):
        code = "int main() { if (true) { return 0; } }"
        validator = SyntaxValidator()
        ok, errors = validator.validate_code(code)
        self.assertTrue(ok)
        self.assertEqual(len(errors), 0)

    def test_mismatched_braces(self):
        code = "int main() { if (true) { return 0; }"
        validator = SyntaxValidator()
        ok, errors = validator.validate_code(code)
        self.assertFalse(ok)
        self.assertGreater(len(errors), 0)
        self.assertIn("Unclosed opening character '{'", errors[0])

    def test_malformed_preprocessor(self):
        code = "#include <iostream\nint main() { return 0; }"
        validator = SyntaxValidator()
        ok, errors = validator.validate_code(code)
        self.assertFalse(ok)
        self.assertIn("Malformed #include directive", errors[0])


class TestCompilerAndRegistry(unittest.TestCase):

    @patch("asyncio.create_subprocess_shell")
    def test_gcc_compiler_success(self, mock_shell):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"Success", b""))
        mock_shell.return_value = mock_proc

        compiler = CompilerRegistry.get_compiler("gcc")
        self.assertIsInstance(compiler, GCCCompiler)

        res = asyncio.run(compiler.compile(
            workspace_path="dummy",
            source_files=["main.cpp"],
            timeout=10
        ))
        self.assertTrue(res.success)
        self.assertEqual(res.compiler, "gcc")

    @patch("asyncio.create_subprocess_shell")
    def test_clang_compiler_warning_parse(self, mock_shell):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"some warning output", b"main.cpp:5:10: warning: unused variable 'x'"))
        mock_shell.return_value = mock_proc

        compiler = CompilerRegistry.get_compiler("clang")
        self.assertIsInstance(compiler, ClangCompiler)

        res = asyncio.run(compiler.compile(
            workspace_path="dummy",
            source_files=["main.cpp"],
            timeout=10
        ))
        self.assertTrue(res.success)
        self.assertEqual(len(res.warnings), 1)
        self.assertIn("warning: unused variable 'x'", res.warnings[0])


class TestBuildSystem(unittest.TestCase):

    @patch("asyncio.create_subprocess_shell")
    def test_cmake_configure(self, mock_shell):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"CMake config succeeded", b""))
        mock_shell.return_value = mock_proc

        build_sys = BuildSystemRegistry.get_build_system("cmake")
        self.assertIsInstance(build_sys, CMakeBuildSystem)

        success = asyncio.run(build_sys.configure("dummy", "gcc"))
        self.assertTrue(success)


class TestStaticValidator(unittest.TestCase):

    @patch("backend.core.analysis.engine.AnalysisEngine.analyze_plain")
    def test_static_validation_bug_removed(self, mock_analyze):
        # Mock analysis engine output: target finding exists in 'before' but not 'after'
        finding_before = MagicMock()
        finding_before.rule_id = "null_pointer"
        finding_before.line_number = 10
        
        mock_analyze.side_effect = [
            [finding_before], # before
            [] # after
        ]

        temp_dir = tempfile.TemporaryDirectory()
        orig = os.path.join(temp_dir.name, "orig.cpp")
        patched = os.path.join(temp_dir.name, "patched.cpp")
        with open(orig, "w") as f: f.write("orig")
        with open(patched, "w") as f: f.write("patched")

        validator = StaticValidator()
        res = validator.validate_patch(
            original_file_path=orig,
            patched_file_path=patched,
            original_finding={"rule_id": "null_pointer", "line_number": 10}
        )

        self.assertTrue(res.original_bug_removed)
        self.assertEqual(res.new_findings_count, 0)
        temp_dir.cleanup()


class TestRegression(unittest.TestCase):

    def test_discovery_ctest(self):
        temp_dir = tempfile.TemporaryDirectory()
        build_path = os.path.join(temp_dir.name, "build")
        os.makedirs(build_path, exist_ok=True)
        with open(os.path.join(build_path, "CTestTestfile.cmake"), "w") as f:
            f.write("# ctest")

        discovery = TestDiscovery()
        tests = discovery.discover_tests(temp_dir.name, "build")
        self.assertEqual(len(tests), 1)
        self.assertEqual(tests[0]["framework"], "ctest")
        temp_dir.cleanup()

    @patch("asyncio.create_subprocess_shell")
    def test_runner_ctest_output(self, mock_shell):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"100% tests passed, 0 tests failed out of 15", b""))
        mock_shell.return_value = mock_proc

        runner = RegressionRunner()
        res = asyncio.run(runner.run_tests(
            workspace_path="dummy",
            discovered_tests=[{"name": "test_suite", "cmd": "ctest", "framework": "ctest"}],
            timeout=10
        ))
        self.assertTrue(res.success)
        self.assertEqual(res.pass_count, 15)
        self.assertEqual(res.fail_count, 0)


class TestMetricsCalculator(unittest.TestCase):

    def test_scoring_success(self):
        calc = QualityMetricsCalculator()
        metrics = calc.compute_metrics(
            compilation_success=True,
            syntax_success=True,
            bug_removed=True,
            regression_success=True,
            new_bug_count=0,
            warning_delta=0,
            lines_changed=5,
            patch_size=100,
            duration_ms=150.0
        )
        self.assertEqual(metrics.score, 0.8) # 0.4 bug removed + 0.3 tests + 0.1 simplicity

    def test_scoring_compile_fail(self):
        calc = QualityMetricsCalculator()
        metrics = calc.compute_metrics(
            compilation_success=False,
            syntax_success=True,
            bug_removed=True,
            regression_success=True,
            new_bug_count=0,
            warning_delta=0,
            lines_changed=5,
            patch_size=100,
            duration_ms=150.0
        )
        self.assertEqual(metrics.score, 0.0)

    def test_disabled_regression_does_not_cap_the_score(self):
        """
        A dimension that was never evaluated must be excluded, not counted as
        a failure. Scoring it zero capped the total at 0.5, below the default
        0.7 acceptance threshold, so no candidate could ever be accepted with
        regression testing turned off.
        """
        calc = QualityMetricsCalculator()
        metrics = calc.compute_metrics(
            compilation_success=True,
            syntax_success=True,
            bug_removed=True,
            regression_success=False,
            new_bug_count=0,
            warning_delta=0,
            lines_changed=5,
            patch_size=100,
            duration_ms=150.0,
            regression_evaluated=False,
        )
        self.assertEqual(metrics.score, 0.8)
        self.assertGreaterEqual(metrics.score, PatchValidationConfig().min_acceptance_score)

    def test_disabled_static_analysis_does_not_cap_the_score(self):
        calc = QualityMetricsCalculator()
        metrics = calc.compute_metrics(
            compilation_success=True,
            syntax_success=True,
            bug_removed=False,
            regression_success=True,
            new_bug_count=0,
            warning_delta=0,
            lines_changed=5,
            patch_size=100,
            duration_ms=150.0,
            static_evaluated=False,
        )
        self.assertEqual(metrics.score, 0.8)

    def test_evaluated_failure_still_penalised(self):
        """Excluding *unevaluated* dimensions must not excuse real failures."""
        calc = QualityMetricsCalculator()
        metrics = calc.compute_metrics(
            compilation_success=True,
            syntax_success=True,
            bug_removed=True,
            regression_success=False,
            new_bug_count=0,
            warning_delta=0,
            lines_changed=5,
            patch_size=100,
            duration_ms=150.0,
            regression_evaluated=True,
        )
        self.assertLess(metrics.score, 0.7)

    def test_new_bugs_penalty_applies(self):
        calc = QualityMetricsCalculator()
        metrics = calc.compute_metrics(
            True, True, True, True, 2, 0, 5, 100, 150.0
        )
        self.assertAlmostEqual(metrics.score, 0.5, places=3)


class TestSyntaxValidatorLineNumbers(unittest.TestCase):
    """
    _clean_code used to delete comments and literals, which shifted every
    character index out of alignment with the original text the line numbers
    were computed from.
    """

    def setUp(self):
        from backend.core.patch_validation.syntax_validator import SyntaxValidator
        self.validator = SyntaxValidator()

    def test_braces_inside_comments_and_strings_are_ignored(self):
        code = (
            '// a comment with { and ( \n'
            'const char* s = "} ) {";\n'
            '/* block } { */\n'
            'void f() {\n'
            '}\n'
        )
        ok, errors = self.validator.validate_code(code)
        self.assertTrue(ok, f"unexpected errors: {errors}")

    def test_unbalanced_brace_reports_correct_line(self):
        code = (
            '// filler comment\n'
            'const char* s = "a string";\n'
            '/* multi\n'
            '   line */\n'
            'void f() {\n'
            '    g();\n'
        )
        ok, errors = self.validator.validate_code(code)
        self.assertFalse(ok)
        self.assertEqual(len(errors), 1)
        self.assertIn("line 5", errors[0])

    def test_mismatched_closer_reports_correct_line(self):
        code = 'int a = 1;\n// comment\nvoid f() (\n}\n'
        ok, errors = self.validator.validate_code(code)
        self.assertFalse(ok)
        self.assertTrue(any("line 4" in e for e in errors), errors)


class TestCandidateRanker(unittest.TestCase):

    def test_ranking_sort(self):
        calc = QualityMetricsCalculator()
        metrics_1 = calc.compute_metrics(True, True, True, True, 0, 0, 5, 100, 150.0) # score 0.8
        metrics_2 = calc.compute_metrics(True, True, False, True, 0, 0, 5, 100, 150.0) # score 0.4

        ranker = CandidateRanker()
        rankings = ranker.rank_candidates({
            "cand_1": metrics_1,
            "cand_2": metrics_2
        }, min_score=0.5)

        self.assertEqual(rankings[0].candidate_id, "cand_1")
        self.assertTrue(rankings[0].winner)
        self.assertEqual(rankings[1].candidate_id, "cand_2")
        self.assertFalse(rankings[1].winner)


class TestReportGenerator(unittest.TestCase):

    def test_report_formatters(self):
        report = ValidationReport(
            patch_id="patch-123",
            bug_id="bug-456",
            winner_candidate_id="cand-1",
            accepted=True
        )
        report.rankings = [CandidateRanking(
            candidate_id="cand-1", score=0.8, rank=1, winner=True, reasons=["Success"]
        )]
        report.metrics = {
            "cand-1": ValidationMetrics(
                compilation_success=True, syntax_success=True, bug_removal_rate=1.0,
                regression_success=True, score=0.8, lines_changed=5, duration_ms=100.0
            )
        }

        md = ValidationReportGenerator.to_markdown(report)
        self.assertIn("Argus Patch Validation Report", md)
        self.assertIn("**Winner Candidate:** `cand-1`", md)
        
        json_str = ValidationReportGenerator.to_json(report)
        self.assertIn("winner_candidate_id", json_str)

        sarif_str = ValidationReportGenerator.to_sarif(report)
        self.assertIn("Argus Patch Validator", sarif_str)


class TestRollbackManager(unittest.TestCase):

    def test_rollback_file_restoration(self):
        temp_dir = tempfile.TemporaryDirectory()
        file_path = os.path.join(temp_dir.name, "test.txt")
        with open(file_path, "w") as f:
            f.write("original content")

        mgr = RollbackManager()
        mgr.backup_file(file_path)

        with open(file_path, "w") as f:
            f.write("modified content")

        mgr.rollback_all()

        with open(file_path, "r") as f:
            content = f.read()
        self.assertEqual(content, "original content")
        temp_dir.cleanup()


class TestValidationEngineEndToEnd(unittest.TestCase):

    @patch("backend.core.patch_validation.workspace_manager.WorkspaceManager.__enter__")
    @patch("backend.core.patch_validation.validator.CandidateValidator.validate_candidate")
    def test_engine_run_with_mocks(self, mock_validate, mock_ws_enter):
        # Mock workspace context manager return value
        mock_ws = MagicMock()
        mock_ws.path = "mock_path"
        mock_ws.original_path = "mock_orig_path"
        mock_ws_enter.return_value = mock_ws

        # Mock validator outcome
        metrics = ValidationMetrics(
            compilation_success=True, syntax_success=True, bug_removal_rate=1.0,
            regression_success=True, score=0.8, lines_changed=5, duration_ms=120.0
        )
        collector = DiagnosticsCollector()
        mock_validate.return_value = (metrics, collector)

        # Build StructuredPatch for testing
        candidate = PatchCandidate(
            candidate_id="cand-abc",
            original_code="original",
            patched_code="patched"
        )
        file_patch = FilePatch(
            file_path="src/main.cpp",
            candidates=[candidate]
        )
        patch_obj = StructuredPatch(
            patch_id="patch-abc",
            bug_id="bug-1",
            file_patches=[file_patch]
        )

        config = PatchValidationConfig(workspace_type="temp_dir", min_acceptance_score=0.5)
        engine = ValidationEngine(config)

        report = asyncio.run(engine.validate_patch(
            patch=patch_obj,
            original_code_path="dummy_original"
        ))

        self.assertTrue(report.accepted)
        self.assertEqual(report.winner_candidate_id, "cand-abc")
        self.assertEqual(file_patch.best_candidate_index, 0)
