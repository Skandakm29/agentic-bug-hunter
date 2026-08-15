import os
import sys
import unittest
from pathlib import Path

# Inject parent directory into sys.path to allow importing from 'backend.core...'
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.core.analysis.engine import AnalysisEngine


class TestStaticEngine(unittest.TestCase):

    def setUp(self):
        self.engine = AnalysisEngine()

    def test_unknown_methods(self):
        # Valid RDI methods should pass
        valid_code = "rdi.pin('VDD').vForce(1.8).iMeas();"
        findings = self.engine.analyze_plain(valid_code)
        self.assertEqual(len(findings), 0)

        # Chaining with groundForce should pass
        ground_code = "rdi.pin('VSS').groundForce();"
        findings = self.engine.analyze_plain(ground_code)
        self.assertEqual(len(findings), 0)

        # Standard C++ vector calls should NOT be flagged (False Positive Protection)
        vector_code = "std::vector<int> v;\nv.push_back(10);"
        findings = self.engine.analyze_plain(vector_code)
        self.assertEqual(len(findings), 0)

        # Unknown RDI methods should be flagged
        invalid_code = "rdi.pin('OUT').hackMethod(0.5);"
        findings = self.engine.analyze_plain(invalid_code)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].rule_id, "suspicious_method_name")
        self.assertIn("hackMethod", findings[0].description)

    def test_unmatched_rdi_blocks(self):
        # Matching blocks should pass
        valid_code = "RDI_BEGIN\n  rdi.wait(10);\nRDI_END"
        findings = self.engine.analyze_plain(valid_code)
        self.assertEqual(len(findings), 0)

        # Mismatched blocks should fail
        invalid_code = "RDI_BEGIN\n  rdi.wait(10);"
        findings = self.engine.analyze_plain(invalid_code)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].rule_id, "rdi_block_mismatch")

    def test_incomplete_chaining(self):
        # Inline terminated chain should pass
        valid_code = "rdi.pin('VDD').vForce(1.8);"
        findings = self.engine.analyze_plain(valid_code)
        self.assertEqual(len(findings), 0)

        # Multiline builder chaining should pass (False Positive Protection)
        multiline_code = "rdi.pin('VDD')\n  .vForce(1.8)\n  .iMeas();"
        findings = self.engine.analyze_plain(multiline_code)
        self.assertEqual(len(findings), 0)

        # Missing terminator without continuation should be flagged
        invalid_code = "rdi.pin('VDD').vForce(1.8)"
        findings = self.engine.analyze_plain(invalid_code)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].rule_id, "incomplete_chain")

    def test_null_pointer(self):
        # Declared and allocated pointer should pass
        valid_code = "int *ptr;\nptr = malloc(sizeof(int));\n*ptr = 10;"
        findings = self.engine.analyze_plain(valid_code)
        self.assertEqual(len(findings), 0)

        # Dereferenced pointer without allocation should be flagged
        invalid_code = "int *ptr;\n*ptr = 10;"
        findings = self.engine.analyze_plain(invalid_code)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].rule_id, "null_pointer")
        self.assertIn("ptr", findings[0].description)

    def test_blocking_delay_in_isr(self):
        # Normal function delay should pass
        normal_code = "void sleep_func() {\n  delay(100);\n}"
        findings = self.engine.analyze_plain(normal_code)
        self.assertEqual(len(findings), 0)

        # Delay inside ISR context should be flagged
        isr_code = "void IRAM_ATTR my_isr() {\n  delay(100);\n}"
        findings = self.engine.analyze_plain(isr_code)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].rule_id, "blocking_in_isr")

        # Nested braces inside ISR should NOT cause early exits from ISR context (False Positive Protection)
        nested_isr_code = """
        void IRAM_ATTR my_isr() {
            if (flag) {
                flag = false;
            }
            delay(100);
        }
        """
        findings = self.engine.analyze_plain(nested_isr_code)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].rule_id, "blocking_in_isr")


if __name__ == "__main__":
    unittest.main()
