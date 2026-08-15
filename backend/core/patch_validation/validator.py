import os
import time
import logging
from typing import Tuple
from backend.core.patch_validation.configuration import PatchValidationConfig
from backend.core.patch_validation.validation_models import Workspace, ValidationMetrics, CompilationResult, RegressionResult, StaticReanalysisResult
from backend.core.patch_validation.exceptions import PatchValidationError, PatchApplyError
from backend.core.patch_validation.patch_applier import PatchApplier
from backend.core.patch_validation.syntax_validator import SyntaxValidator
from backend.core.patch_validation.build_system import BuildSystemRegistry
from backend.core.patch_validation.static_validator import StaticValidator
from backend.core.patch_validation.test_discovery import TestDiscovery
from backend.core.patch_validation.regression_runner import RegressionRunner
from backend.core.patch_validation.quality_metrics import QualityMetricsCalculator
from backend.core.patch_validation.diagnostics import DiagnosticsCollector
from backend.core.patch_validation.rollback import RollbackManager

logger = logging.getLogger("backend.patch_validation.validator")


class CandidateValidator:
    """Orchestrates the validation pipeline for a single patch candidate."""

    def __init__(self, config: PatchValidationConfig) -> None:
        self.config = config
        self.applier = PatchApplier()
        self.syntax_checker = SyntaxValidator()
        self.static_analyzer = StaticValidator()
        self.test_discoverer = TestDiscovery()
        self.test_runner = RegressionRunner()
        self.metrics_calculator = QualityMetricsCalculator()
        self._rollback_mgr = RollbackManager()

    async def validate_candidate(
        self,
        workspace: Workspace,
        file_path: str,
        candidate,
        original_finding: dict
    ) -> Tuple[ValidationMetrics, DiagnosticsCollector]:
        """
        Validate a single patch candidate.

        Parameters
        ----------
        workspace : Workspace model.
        file_path : Target file path.
        candidate : PatchCandidate.
        original_finding : Dict representation of the target bug finding.
        """
        t_start = time.perf_counter()
        collector = DiagnosticsCollector()
        collector.add_affected_file(file_path)

        # Initialize status variables
        syntax_success = False
        compilation_success = False
        bug_removed = False
        regression_success = False
        new_bug_count = 0
        warning_delta = 0
        # Track whether each optional dimension actually produced a result, so
        # a disabled or crashed stage is excluded from scoring rather than
        # counted as a failure.
        static_evaluated = False
        regression_evaluated = False

        # Target file resolution in workspace
        workspace_file = os.path.join(workspace.path, file_path)
        original_file = os.path.join(workspace.original_path, file_path)

        # ── Step 1: Backup & Patch Application ──────────────────
        t0 = time.perf_counter()
        self._rollback_mgr.backup_file(workspace_file)
        
        try:
            self.applier.apply_patch(workspace.path, file_path, candidate)
            collector.record_timing("patch_application", (time.perf_counter() - t0) * 1000)
        except PatchApplyError as e:
            collector.add_error(f"Patch apply failure: {e}")
            collector.add_suggested_action("Verify diff matches the source code headers.")
            # Rollback immediately on failure
            self._rollback_mgr.rollback_all()
            metrics = self.metrics_calculator.compute_metrics(
                compilation_success=False,
                syntax_success=False,
                bug_removed=False,
                regression_success=False,
                new_bug_count=0,
                warning_delta=0,
                lines_changed=0,
                patch_size=len(candidate.unified_diff) if candidate.unified_diff else 0,
                duration_ms=(time.perf_counter() - t_start) * 1000
            )
            return metrics, collector

        # ── Step 2: Syntax Check ────────────────────────────────
        t0 = time.perf_counter()
        try:
            with open(workspace_file, "r", encoding="utf-8", errors="ignore") as f:
                code_content = f.read()
            
            ok, errors = self.syntax_checker.validate_code(code_content)
            syntax_success = ok
            collector.record_timing("syntax_verification", (time.perf_counter() - t0) * 1000)
            
            if not ok:
                for err in errors:
                    collector.add_error(f"Syntax Error: {err}")
                collector.add_suggested_action("Fix formatting syntax, balanced braces, or macro symbols.")
        except Exception as e:
            collector.add_error(f"Syntax check execution failure: {e}")

        # ── Step 3: Compilation ─────────────────────────────────
        if syntax_success:
            t0 = time.perf_counter()
            build_sys = BuildSystemRegistry.get_build_system(self.config.build_system)
            
            # Configure build system
            config_ok = await build_sys.configure(
                workspace.path, 
                self.config.compiler_type, 
                self.config.parallel_jobs
            )
            
            if config_ok:
                # Trigger build compilation
                compile_res = await build_sys.build(
                    workspace_path=workspace.path,
                    compiler_type=self.config.compiler_type,
                    parallel_jobs=self.config.parallel_jobs,
                    timeout=self.config.timeout_seconds
                )
                compilation_success = compile_res.success
                warning_delta += len(compile_res.warnings)
                
                collector.record_timing("compilation", (time.perf_counter() - t0) * 1000)

                for err in compile_res.errors:
                    collector.add_error(f"Compile Error: {err}")
                for wrn in compile_res.warnings:
                    collector.add_warning(f"Compiler Warning: {wrn}")
                
                if not compilation_success:
                    collector.add_suggested_action("Examine compiler logs for build issues or type errors.")
            else:
                collector.add_error("Build system configuration step failed.")
                collector.add_suggested_action("Verify build system configurations (CMakeLists.txt/Makefile) are correct.")

        # ── Step 4: Static Validation (Reanalysis) ─────────────
        if compilation_success and self.config.static_analysis_enabled:
            t0 = time.perf_counter()
            try:
                # If we are in-place, the original file is modified, so we pass backup path
                # Since static_validator needs both, we can retrieve original file from original_path
                # But note: in temp_dir, workspace has modified file, original_path has unmodified file
                reanalysis_res = self.static_analyzer.validate_patch(
                    original_file_path=original_file,
                    patched_file_path=workspace_file,
                    original_finding=original_finding
                )
                
                bug_removed = reanalysis_res.original_bug_removed
                new_bug_count = reanalysis_res.new_findings_count
                warning_delta += reanalysis_res.warning_delta
                static_evaluated = True

                collector.record_timing("static_validation", (time.perf_counter() - t0) * 1000)
                
                if not bug_removed:
                    collector.add_warning("Static verification: The original bug is still detected after patching.")
                if new_bug_count > 0:
                    collector.add_warning(f"Static verification: Patch introduced {new_bug_count} new static warnings.")
            except Exception as e:
                collector.add_error(f"Static validation execution failure: {e}")

        # ── Step 5: Regression Testing ─────────────────────────
        if compilation_success and self.config.regression_enabled:
            t0 = time.perf_counter()
            try:
                tests = self.test_discoverer.discover_tests(workspace.path, self.config.build_directory)
                regression_res = await self.test_runner.run_tests(
                    workspace_path=workspace.path,
                    discovered_tests=tests,
                    timeout=self.config.timeout_seconds
                )
                regression_success = regression_res.success
                regression_evaluated = True

                collector.record_timing("regression_testing", (time.perf_counter() - t0) * 1000)

                if not regression_success:
                    collector.add_error(f"Regression failures: {regression_res.details}")
                    collector.add_suggested_action("Check test cases to trace introduced side effects.")
            except Exception as e:
                collector.add_error(f"Regression testing runner failure: {e}")

        # Clean up files in workspace (roll back so next candidate starts clean)
        self._rollback_mgr.rollback_all()

        # Compute lines changed and patch size
        lines_changed = 0
        if candidate.patched_code:
            lines_changed = len(candidate.patched_code.splitlines())
        patch_size = len(candidate.unified_diff) if candidate.unified_diff else 0

        duration_ms = (time.perf_counter() - t_start) * 1000
        metrics = self.metrics_calculator.compute_metrics(
            compilation_success=compilation_success,
            syntax_success=syntax_success,
            bug_removed=bug_removed,
            regression_success=regression_success,
            new_bug_count=new_bug_count,
            warning_delta=warning_delta,
            lines_changed=lines_changed,
            patch_size=patch_size,
            duration_ms=duration_ms,
            static_evaluated=static_evaluated,
            regression_evaluated=regression_evaluated,
        )

        return metrics, collector
