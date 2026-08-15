import os
import time
import logging
import asyncio
import re
from typing import List, Dict, Tuple
from backend.core.patch_validation.validation_models import RegressionResult
from backend.core.patch_validation.exceptions import TestError

logger = logging.getLogger("backend.patch_validation.regression_runner")


class RegressionRunner:
    """Executes discovered regression test suites and parses execution metrics."""

    async def run_tests(
        self,
        workspace_path: str,
        discovered_tests: List[Dict[str, str]],
        timeout: int = 60,
    ) -> RegressionResult:
        """
        Run discovered test suites.

        Parameters
        ----------
        workspace_path : Path to workspace folder.
        discovered_tests : List of test dicts.
        timeout : Total timeout in seconds.
        """
        if not discovered_tests:
            logger.info("No tests discovered. Skipping regression runner.")
            return RegressionResult(
                success=True,
                pass_count=0,
                fail_count=0,
                details="No tests discovered in the workspace."
            )

        start_time = time.perf_counter()
        total_pass = 0
        total_fail = 0
        total_skipped = 0
        details_list = []
        all_passed = True

        for test in discovered_tests:
            name = test["name"]
            cmd = test["cmd"]
            framework = test["framework"]

            logger.info(f"Running test suite '{name}' via command: {cmd}")
            try:
                proc = await asyncio.create_subprocess_shell(
                    cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=workspace_path
                )
                
                try:
                    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                    stdout_str = stdout.decode("utf-8", errors="ignore")
                    stderr_str = stderr.decode("utf-8", errors="ignore")
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
                    all_passed = False
                    total_fail += 1
                    details_list.append(f"Test suite '{name}' timed out.")
                    continue

                suite_success = proc.returncode == 0
                if not suite_success:
                    all_passed = False

                suite_pass, suite_fail, suite_skip = self._parse_test_counts(
                    stdout_str + stderr_str, framework, suite_success
                )
                total_pass += suite_pass
                total_fail += suite_fail
                total_skipped += suite_skip

                details_list.append(
                    f"Suite '{name}': exit_code={proc.returncode}, "
                    f"pass={suite_pass}, fail={suite_fail}, skip={suite_skip}"
                )
            except Exception as e:
                logger.error(f"Error running test '{name}': {e}")
                all_passed = False
                total_fail += 1
                details_list.append(f"Suite '{name}' execution exception: {e}")

        execution_time_ms = round((time.perf_counter() - start_time) * 1000, 2)
        return RegressionResult(
            success=all_passed,
            pass_count=total_pass,
            fail_count=total_fail,
            skipped=total_skipped,
            execution_time_ms=execution_time_ms,
            details="\n".join(details_list)
        )

    def _parse_test_counts(self, output: str, framework: str, suite_success: bool) -> Tuple[int, int, int]:
        """Parse stdout/stderr to count passed, failed, skipped tests."""
        # 1. CTest parsing
        if framework == "ctest":
            # Example: "100% tests passed, 0 tests failed out of 10"
            m = re.search(r"(\d+)\s+tests\s+failed\s+out\s+of\s+(\d+)", output, re.IGNORECASE)
            if m:
                failed = int(m.group(1))
                total = int(m.group(2))
                passed = total - failed
                return passed, failed, 0
                
            # Example: "9/10 tests passed (90%)"
            m = re.search(r"(\d+)/(\d+)\s+tests\s+passed", output, re.IGNORECASE)
            if m:
                passed = int(m.group(1))
                total = int(m.group(2))
                return passed, total - passed, 0

        # 2. GoogleTest parsing
        elif framework == "gtest":
            # Example: "[  PASSED  ] 14 tests."
            # Example: "[  FAILED  ] 2 tests, listed below:"
            passed = 0
            failed = 0
            m_pass = re.search(r"\[\s+PASSED\s+\]\s+(\d+)\s+test", output)
            if m_pass:
                passed = int(m_pass.group(1))
            m_fail = re.search(r"\[\s+FAILED\s+\]\s+(\d+)\s+test", output)
            if m_fail:
                failed = int(m_fail.group(1))
            
            if passed > 0 or failed > 0:
                return passed, failed, 0

        # 3. Catch2 parsing
        elif framework == "catch2":
            # Example: "All tests passed (10 assertions in 2 test cases)"
            # Example: "Failed 1 test case, failed 2 assertions"
            m_pass = re.search(r"All tests passed\s+\(\s*\d+\s+assertions\s+in\s+(\d+)\s+test\s+cases\s*\)", output, re.IGNORECASE)
            if m_pass:
                return int(m_pass.group(1)), 0, 0
                
            m_fail = re.search(r"Failed\s+(\d+)\s+test\s+case", output, re.IGNORECASE)
            if m_fail:
                return 0, int(m_fail.group(1)), 0

        # Fallback to simple success/failure binary counts
        if suite_success:
            return 1, 0, 0
        else:
            return 0, 1, 0
