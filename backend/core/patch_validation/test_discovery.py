import os
import logging
from typing import List, Dict

logger = logging.getLogger("backend.patch_validation.test_discovery")


class TestDiscovery:
    """Discovers test suites and test executables in the workspace."""

    def discover_tests(self, workspace_path: str, build_dir: str = "build") -> List[Dict[str, str]]:
        """
        Scan workspace for ctest, gtest, or custom script test targets.

        Returns
        -------
        List of dicts containing 'name', 'cmd', and 'framework'.
        """
        tests = []

        # 1. Look for CTest config files
        ctest_dir = os.path.join(workspace_path, build_dir)
        if os.path.exists(os.path.join(ctest_dir, "CTestTestfile.cmake")):
            tests.append({
                "name": "ctest",
                "cmd": "ctest --output-on-failure",
                "framework": "ctest"
            })
            logger.info("Discovered CTest configuration.")
            return tests

        # 2. Look for common test script files
        common_scripts = [
            "run_tests.sh", "test.sh", "tests.sh", 
            "run_tests.bat", "test.bat", "run_tests.py"
        ]
        for script in common_scripts:
            script_path = os.path.join(workspace_path, script)
            if os.path.exists(script_path):
                cmd = f"python {script}" if script.endswith(".py") else f"./{script}"
                if os.name == "nt" and script.endswith(".sh"):
                    # On Windows, try to run via bash if bash is available
                    cmd = f"bash {script}"
                elif os.name == "nt" and (script.endswith(".bat") or script.endswith(".cmd")):
                    cmd = script

                tests.append({
                    "name": f"script_{script}",
                    "cmd": cmd,
                    "framework": "script"
                })
                logger.info(f"Discovered test script: {script}")

        # 3. Look for gtest/catch2 executables in build folder
        build_path = os.path.join(workspace_path, build_dir)
        if os.path.exists(build_path):
            for root, _, files in os.walk(build_path):
                for file in files:
                    # Look for names containing 'test' or 'run' and having executable suffixes
                    file_lower = file.lower()
                    if "test" in file_lower or "runner" in file_lower:
                        is_exec = False
                        if os.name == "nt":
                            if file_lower.endswith(".exe"):
                                is_exec = True
                        else:
                            # Check executable bits
                            full_path = os.path.join(root, file)
                            if os.access(full_path, os.X_OK):
                                is_exec = True

                        if is_exec:
                            rel_path = os.path.relpath(os.path.join(root, file), workspace_path)
                            # Prefix with ./ on Unix platforms
                            cmd_prefix = "" if os.name == "nt" else "./"
                            tests.append({
                                "name": f"binary_{file}",
                                "cmd": f"{cmd_prefix}{rel_path}",
                                "framework": "gtest" if "gtest" in file_lower else "catch2" if "catch" in file_lower else "binary"
                            })
                            logger.info(f"Discovered test binary: {file}")

        return tests
