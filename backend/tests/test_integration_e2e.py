"""
End-to-End Integration Suite
=============================
Drives the full pipeline against a real file on disk:

    detect (3C.2) -> generate (3D.1) -> apply + compile + revalidate (3D.2)

The LLM provider is mocked -- the point is to exercise the wiring *between*
phases, which unit tests with mocked neighbours cannot cover. Compilation
uses a real toolchain and is skipped when one is unavailable.
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

# Inject project root so imports resolve correctly
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.core.analysis.engine import AnalysisEngine
from backend.core.patch_generation import PatchGenerationEngine, PatchGenerationConfig
from backend.core.patch_validation import ValidationEngine, PatchValidationConfig
from backend.core.patch_validation.static_validator import StaticValidator
from backend.core.patch_validation.validation_report import ValidationReportGenerator


ORIGINAL = """#include <cstdint>

uint8_t total = 0;

int accumulate(const uint8_t* samples, int n) {
    for (int i = 0; i < n; ++i) {
        total = total + samples[i];
    }
    return total;
}

int main() { return 0; }
"""

HAS_GPP = shutil.which("g++") is not None


def _mock_provider(original_code: str, patched_code: str, confidence: float = 0.9):
    provider = AsyncMock()
    provider.generate_completion_async.return_value = json.dumps({
        "candidates": [{
            "candidate_id": "C1",
            "reasoning": "Widen the accumulator so it cannot wrap at 255.",
            "original_code": original_code,
            "patched_code": patched_code,
            "confidence": confidence,
            "advantages": ["No wraparound"],
            "disadvantages": ["Three extra bytes"],
            "estimated_risk": 0.1,
        }]
    })
    return provider


# ---------------------------------------------------------------------------
# StaticValidator before/after comparison
# ---------------------------------------------------------------------------


class TestStaticValidatorComparison(unittest.TestCase):
    """
    Matching findings by "same rule within +/-5 lines" conflated the bug being
    fixed with an unrelated occurrence of the same rule nearby. In ORIGINAL,
    `int accumulate(const uint8_t* samples, ...)` also trips overflow_risk
    (it contains "uint8_t" and "acc"), which sits 2 lines from the real
    defect -- so a correct patch was still reported as "bug not removed".
    """

    def _compare(self, patched: str, finding: dict):
        with tempfile.TemporaryDirectory() as d:
            before = os.path.join(d, "before.cpp")
            after = os.path.join(d, "after.cpp")
            Path(before).write_text(ORIGINAL, encoding="utf-8")
            Path(after).write_text(patched, encoding="utf-8")
            return StaticValidator().validate_patch(before, after, finding)

    @property
    def _target(self):
        return {
            "rule_id": "overflow_risk",
            "line_number": 3,
            "line_text": "uint8_t total = 0;",
        }

    def test_real_fix_is_recognised(self):
        fixed = ORIGINAL.replace("uint8_t total = 0;", "uint32_t total = 0;")
        result = self._compare(fixed, self._target)
        self.assertTrue(result.original_bug_removed)
        self.assertEqual(result.new_findings_count, 0)

    def test_neighbouring_same_rule_hit_does_not_mask_the_fix(self):
        """
        Fixing one of two genuine same-rule findings must still register as a
        fix. The surviving occurrence must not be mistaken for the one that
        was repaired.
        """
        before = (
            "#include <cstdint>\n"
            "\n"
            "uint8_t total = 0;\n"
            "uint8_t sample_count = 0;\n"
            "\n"
            "int main() { return 0; }\n"
        )
        after = before.replace("uint8_t total = 0;", "uint32_t total = 0;")
        target = {
            "rule_id": "overflow_risk",
            "line_number": 3,
            "line_text": "uint8_t total = 0;",
        }

        with tempfile.TemporaryDirectory() as d:
            before_path = os.path.join(d, "before.cpp")
            after_path = os.path.join(d, "after.cpp")
            Path(before_path).write_text(before, encoding="utf-8")
            Path(after_path).write_text(after, encoding="utf-8")
            result = StaticValidator().validate_patch(before_path, after_path, target)

        self.assertEqual(result.before_findings_count, 2)
        # The sample_count accumulator legitimately survives the patch...
        self.assertEqual(result.after_findings_count, 1)
        # ...but must not mask the fix to `total`.
        self.assertTrue(result.original_bug_removed)
        self.assertEqual(result.new_findings_count, 0)

    def test_cosmetic_change_is_not_a_fix(self):
        not_fixed = ORIGINAL.replace("int n", "int count")
        result = self._compare(not_fixed, self._target)
        self.assertFalse(result.original_bug_removed)

    def test_appending_a_comment_is_not_a_fix(self):
        """
        Signature-only matching would see the old flagged text disappear and
        wrongly call this fixed, so the per-rule count must agree too.
        """
        not_fixed = ORIGINAL.replace(
            "uint8_t total = 0;", "uint8_t total = 0; // reviewed"
        )
        result = self._compare(not_fixed, self._target)
        self.assertFalse(result.original_bug_removed)

    def test_unchanged_file_is_not_a_fix(self):
        result = self._compare(ORIGINAL, self._target)
        self.assertFalse(result.original_bug_removed)

    def test_falls_back_to_counting_without_line_text(self):
        fixed = ORIGINAL.replace("uint8_t total = 0;", "uint32_t total = 0;")
        result = self._compare(fixed, {"rule_id": "overflow_risk", "line_number": 3})
        self.assertTrue(result.original_bug_removed)

    def test_introduced_finding_is_counted(self):
        # Replace the guarded loop with an unguarded pointer dereference.
        broken = ORIGINAL.replace(
            "uint8_t total = 0;",
            "uint32_t total = 0;\nint *leaked;\nint sink = *leaked;",
        )
        result = self._compare(broken, self._target)
        self.assertTrue(result.original_bug_removed)
        self.assertGreater(result.new_findings_count, 0)


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------


@unittest.skipUnless(HAS_GPP, "g++ not available on PATH")
class TestGenerateValidateChain(unittest.TestCase):

    def _run_chain(self, patched_code: str, **config_kw):
        async def main():
            with tempfile.TemporaryDirectory() as repo:
                Path(repo, "main.cpp").write_text(ORIGINAL, encoding="utf-8")

                findings = AnalysisEngine().analyze_plain(
                    ORIGINAL, "cpp", file_path="main.cpp"
                )
                target = next(f for f in findings if f.rule_id == "overflow_risk")

                engine = PatchGenerationEngine(
                    _mock_provider("uint8_t total = 0;", patched_code),
                    PatchGenerationConfig(candidate_count=1),
                )
                patch = await engine.generate(
                    finding=target, code=ORIGINAL, bug_id="BUG-OVF"
                )

                cfg = dict(
                    compiler_type="gcc",
                    build_system="none",
                    regression_enabled=False,
                    static_analysis_enabled=True,
                    min_acceptance_score=0.7,
                )
                cfg.update(config_kw)
                report = await ValidationEngine(
                    PatchValidationConfig(**cfg)
                ).validate_patch(patch=patch, original_code_path=repo)

                untouched = Path(repo, "main.cpp").read_text(encoding="utf-8")
                return patch, report, untouched

        return asyncio.run(main())

    def test_correct_patch_is_accepted(self):
        patch, report, _ = self._run_chain("uint32_t total = 0;")
        candidate = patch.file_patches[0].candidates[0]
        metrics = report.metrics[candidate.candidate_id]

        self.assertTrue(metrics.syntax_success, report.diagnostics.errors)
        self.assertTrue(metrics.compilation_success, report.diagnostics.errors)
        self.assertEqual(metrics.bug_removal_rate, 1.0)
        self.assertTrue(report.accepted)
        self.assertEqual(report.winner_candidate_id, candidate.candidate_id)

    def test_original_repository_is_never_modified(self):
        _, _, untouched = self._run_chain("uint32_t total = 0;")
        self.assertEqual(untouched, ORIGINAL)

    def test_patch_that_does_not_compile_is_rejected(self):
        _, report, _ = self._run_chain("uint32_t total = ;")
        self.assertFalse(report.accepted)
        self.assertIsNone(report.winner_candidate_id)

    def test_patch_that_does_not_fix_is_rejected(self):
        # Compiles fine, but leaves the uint8_t accumulator in place.
        _, report, _ = self._run_chain("uint8_t total = 0; // reviewed")
        self.assertFalse(report.accepted)

    def test_reports_render_in_all_formats(self):
        _, report, _ = self._run_chain("uint32_t total = 0;")
        markdown = ValidationReportGenerator.to_markdown(report)
        self.assertIn("Argus Patch Validation Report", markdown)

        parsed = json.loads(ValidationReportGenerator.to_json(report))
        self.assertTrue(parsed["accepted"])

        sarif = json.loads(ValidationReportGenerator.to_sarif(report))
        self.assertEqual(sarif["version"], "2.1.0")


@unittest.skipUnless(HAS_GPP, "g++ not available on PATH")
class TestAutonomousRepairLoopEndToEnd(unittest.TestCase):
    """
    The Phase 3D.3 loop driving the real 3D.1 and 3D.2 engines.

    RepairLoop used to construct ValidationEngine() with no arguments, pinning
    every session to cmake + gcc + regression testing. On a project with any
    other toolchain the build step failed, validation scored 0, and no
    candidate could clear the acceptance threshold.
    """

    def _run(self, patched_code, **repair_kw):
        from backend.core.autonomous_repair import (
            RepairConfiguration,
            RepairOrchestrator,
        )

        async def main():
            with tempfile.TemporaryDirectory() as repo:
                Path(repo, "main.cpp").write_text(ORIGINAL, encoding="utf-8")
                finding = next(
                    f for f in AnalysisEngine().analyze_plain(
                        ORIGINAL, "cpp", file_path="main.cpp"
                    )
                    if f.rule_id == "overflow_risk"
                )
                cfg = dict(max_iterations=2, acceptance_threshold=0.75)
                cfg.update(repair_kw)
                orchestrator = RepairOrchestrator(
                    _mock_provider("uint8_t total = 0;", patched_code),
                    RepairConfiguration(**cfg),
                    validation_config=PatchValidationConfig(
                        compiler_type="gcc",
                        build_system="none",
                        regression_enabled=False,
                        static_analysis_enabled=True,
                    ),
                )
                return await orchestrator.run(
                    finding=finding,
                    code=ORIGINAL,
                    original_code_path=repo,
                    bug_id="BUG-OVF",
                )

        return asyncio.run(main())

    def test_loop_accepts_a_correct_repair(self):
        from backend.core.autonomous_repair import TerminationReason

        session = self._run("uint32_t total = 0;")
        self.assertTrue(session.accepted)
        self.assertEqual(session.termination_reason, TerminationReason.ACCEPTED)
        self.assertGreaterEqual(session.best_composite_score, 0.75)
        self.assertEqual(len(session.iterations), 1)

    def test_session_carries_report_and_audit_trail(self):
        session = self._run("uint32_t total = 0;")
        self.assertIsNotNone(session.report)
        self.assertIn("BUG-OVF", session.report.executive_summary)
        self.assertTrue(session.report.why_candidate_won)
        self.assertTrue(session.audit_trail_jsonl)
        self.assertIn("session_start", session.audit_trail_jsonl)

    def test_audit_trail_replays(self):
        from backend.core.autonomous_repair.audit import AuditTrail

        session = self._run("uint32_t total = 0;")
        events = AuditTrail.replay(session.audit_trail_jsonl)
        self.assertEqual(len(events), len(session.audit_trail_jsonl.splitlines()))

    def test_loop_rejects_a_non_fix_and_stops(self):
        from backend.core.autonomous_repair import TerminationReason

        session = self._run("uint8_t total = 0; // reviewed")
        self.assertFalse(session.accepted)
        self.assertNotEqual(session.termination_reason, TerminationReason.ACCEPTED)
        self.assertLessEqual(len(session.iterations), 2)


if __name__ == "__main__":
    unittest.main()
