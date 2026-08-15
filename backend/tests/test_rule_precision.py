"""
Rule Precision Regression Suite
================================
Pins the false positives that made the rule set unusable on real code, and
the true positives that must survive fixing them.

Baseline: scanning the 265 C headers shipped with CPython 3.13 produced 110
findings, 109 of them ``null_pointer``, every one false. Three separate
defects were responsible:

1. ``NullPointerRule`` could not tell a pointer *declarator* (``PyObject
   *type``) from a *dereference* (``*p = 5``).
2. ``OverflowRiskRule`` and ``MissingVolatileRule`` matched keywords as
   substrings of the whole line, so ``int accumulate(...)`` matched "acc"
   and ``int readSensor(...)`` matched "sensor".
3. Pointer tracking was file-global and never reset, so a name declared in
   one function leaked into every later one.
"""
import sys
import time
import unittest
from pathlib import Path

# Inject project root so imports resolve correctly
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.core.analysis.engine import AnalysisEngine
from backend.core.analysis.rules.cdecl import (
    find_assigned_names,
    find_dereferences,
    identifier_components,
    is_function_declaration,
    name_matches,
    parse_variable_declaration,
    strip_comments,
)


class _RuleCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = AnalysisEngine()

    def rules_for(self, code: str):
        return sorted({f.rule_id for f in self.engine.analyze_plain(code)})

    def assertFlags(self, code: str, expected):
        self.assertEqual(self.rules_for(code), sorted(expected), f"code: {code!r}")

    def assertClean(self, code: str):
        self.assertEqual(self.rules_for(code), [], f"expected no findings: {code!r}")


# ---------------------------------------------------------------------------
# cdecl primitives
# ---------------------------------------------------------------------------


class TestIdentifierComponents(unittest.TestCase):
    def test_snake_case(self):
        self.assertEqual(identifier_components("sample_count"), ["sample", "count"])

    def test_camel_case(self):
        self.assertEqual(identifier_components("totalValue"), ["total", "value"])

    def test_acronyms(self):
        self.assertEqual(identifier_components("HTTPResponse"), ["http", "response"])

    def test_single_word_is_not_split_into_prefixes(self):
        """The substring bug: 'accumulate' must not yield 'acc'."""
        self.assertEqual(identifier_components("accumulate"), ["accumulate"])

    def test_name_matches_whole_components_only(self):
        self.assertTrue(name_matches("sample_count", ["count"]))
        self.assertTrue(name_matches("totalBytes", ["total"]))
        self.assertFalse(name_matches("accumulate", ["acc"]))
        self.assertFalse(name_matches("readSensorValue", ["reading"]))


class TestParseVariableDeclaration(unittest.TestCase):
    def test_simple_declaration(self):
        decl = parse_variable_declaration("uint8_t total = 0;")
        self.assertIsNotNone(decl)
        self.assertEqual(decl.name, "total")
        self.assertEqual(decl.type_text, "uint8_t")
        self.assertTrue(decl.has_initializer)
        self.assertEqual(decl.pointer_depth, 0)

    def test_pointer_declaration(self):
        decl = parse_variable_declaration("int *ptr;")
        self.assertEqual(decl.name, "ptr")
        self.assertEqual(decl.pointer_depth, 1)
        self.assertFalse(decl.has_initializer)

    def test_qualified_and_templated(self):
        decl = parse_variable_declaration("static const std::vector<int> values;")
        self.assertEqual(decl.name, "values")

    def test_array_declaration(self):
        decl = parse_variable_declaration("uint8_t buffer[64];")
        self.assertTrue(decl.is_array)

    def test_trailing_line_comment_is_ignored(self):
        decl = parse_variable_declaration("uint8_t total = 0; // reviewed")
        self.assertIsNotNone(decl, "a trailing comment must not hide a declaration")
        self.assertEqual(decl.name, "total")

    def test_inline_block_comment_is_ignored(self):
        decl = parse_variable_declaration("uint8_t total = 0; /* note */")
        self.assertIsNotNone(decl)
        self.assertEqual(decl.name, "total")

    def test_function_prototype_is_not_a_declaration(self):
        self.assertIsNone(parse_variable_declaration("int readSensor(int channel);"))

    def test_call_is_not_a_declaration(self):
        self.assertIsNone(parse_variable_declaration("doWork(a, b);"))

    def test_control_flow_is_not_a_declaration(self):
        self.assertIsNone(parse_variable_declaration("return value;"))

    def test_comparison_is_not_an_initialiser(self):
        self.assertIsNone(parse_variable_declaration("a == b;"))

    def test_prose_is_not_a_declaration(self):
        self.assertIsNone(parse_variable_declaration(
            "   is used to enable certain printing options. The only option currently"
        ))


class TestStripComments(unittest.TestCase):
    def test_line_comment(self):
        self.assertEqual(strip_comments("int x = 1; // note"), "int x = 1;")

    def test_block_comment(self):
        # Interior whitespace is not normalised; the tokenizer ignores it.
        self.assertEqual(
            " ".join(strip_comments("int /* t */ x = 1;").split()), "int x = 1;"
        )

    def test_double_slash_inside_string_is_preserved(self):
        line = 'const char* url = "http://example.com";'
        self.assertEqual(strip_comments(line), line)

    def test_slash_inside_char_literal_is_preserved(self):
        self.assertEqual(strip_comments("char sep = '/';"), "char sep = '/';")

    def test_escaped_quote_inside_string(self):
        line = 'const char* s = "a\\"b"; // tail'
        self.assertEqual(strip_comments(line), 'const char* s = "a\\"b";')


class TestFindDereferences(unittest.TestCase):
    def test_statement_start(self):
        self.assertEqual(find_dereferences("*p = 5;"), {"p"})

    def test_after_assignment(self):
        self.assertEqual(find_dereferences("x = *p;"), {"p"})

    def test_after_return(self):
        self.assertEqual(find_dereferences("return *p;"), {"p"})

    def test_declarator_is_not_a_dereference(self):
        self.assertEqual(find_dereferences("PyObject *type;"), set())

    def test_parameter_declarator_is_not_a_dereference(self):
        self.assertEqual(
            find_dereferences("extern PyObject* GetBases(PyTypeObject *type);"), set()
        )

    def test_cast_style_declarator_is_not_a_dereference(self):
        self.assertEqual(
            find_dereferences("static void store(_Atomic(uintptr_t)*p, int x) {"), set()
        )

    def test_block_comment_continuation_is_skipped(self):
        self.assertEqual(find_dereferences(" * note about p"), set())

    def test_leading_star_deref_is_not_mistaken_for_a_comment(self):
        self.assertEqual(find_dereferences("*ptr = 10;"), {"ptr"})


class TestFindAssignedNames(unittest.TestCase):
    def test_plain_assignment(self):
        self.assertIn("p", find_assigned_names("p = malloc(4);"))

    def test_compound_assignment(self):
        self.assertIn("total", find_assigned_names("total += 1;"))

    def test_declaration_with_initialiser(self):
        self.assertIn("p", find_assigned_names("int *p = &q;"))

    def test_equality_is_not_an_assignment(self):
        self.assertNotIn("a", find_assigned_names("if (a == b) { }"))


# ---------------------------------------------------------------------------
# Rule behaviour: true positives must survive
# ---------------------------------------------------------------------------


class TestTruePositivesPreserved(_RuleCase):
    def test_null_dereference(self):
        self.assertFlags("void f() {\n  int *p;\n  *p = 5;\n}", ["null_pointer"])

    def test_null_dereference_via_return(self):
        self.assertFlags("int f() {\n  int *p;\n  return *p;\n}", ["null_pointer"])

    def test_uint8_accumulator(self):
        self.assertFlags("uint8_t total = 0;", ["overflow_risk"])

    def test_uint8_accumulator_snake_case(self):
        self.assertFlags("uint8_t sample_count = 0;", ["overflow_risk"])

    def test_signed_int_sensor(self):
        self.assertFlags("int sensor_value = 0;", ["type_mismatch"])

    def test_isr_shared_flag_without_volatile(self):
        self.assertFlags(
            "void IRAM_ATTR isr() {\n  bool data_ready = 1;\n}", ["missing_volatile"]
        )

    def test_blocking_call_in_isr(self):
        self.assertFlags(
            "void IRAM_ATTR isr() {\n  delay(100);\n}", ["blocking_in_isr"]
        )

    def test_declaration_with_trailing_comment_still_flagged(self):
        self.assertFlags("uint8_t total = 0; // reviewed", ["overflow_risk"])


# ---------------------------------------------------------------------------
# Rule behaviour: false positives must stay gone
# ---------------------------------------------------------------------------


class TestFalsePositivesEliminated(_RuleCase):
    def test_pointer_parameter_in_prototype(self):
        self.assertClean("extern PyObject* GetBases(PyTypeObject *type);")

    def test_pointer_parameter_dereferenced_in_body(self):
        self.assertClean("static void store(int *p, int x) {\n  *p = x;\n}")

    def test_repeated_prototypes(self):
        self.assertClean(
            "void a(PyTypeObject *type);\n"
            "void b(PyTypeObject *type);\n"
            "void c(PyTypeObject *type);\n"
        )

    def test_allocated_pointer(self):
        self.assertClean("void f() {\n  int *p;\n  p = malloc(4);\n  *p = 5;\n}")

    def test_initialised_pointer(self):
        self.assertClean("void f() {\n  int *p = &q;\n  *p = 5;\n}")

    def test_pointer_names_do_not_leak_between_functions(self):
        self.assertClean(
            "void a() {\n  int *p;\n}\n"
            "void b(int *p) {\n  *p = 1;\n}\n"
        )

    def test_accumulate_function_is_not_an_accumulator_variable(self):
        self.assertClean("int accumulate(const uint8_t* samples, int n) {")

    def test_read_sensor_function_is_not_a_sensor_variable(self):
        self.assertClean("int readSensor(int channel);")

    def test_ready_named_variable_outside_isr_context(self):
        self.assertClean("bool is_ready_state = compute();")

    def test_volatile_declaration_is_accepted(self):
        self.assertClean("void IRAM_ATTR isr() {\n  volatile bool flag = 1;\n}")

    def test_prose_inside_block_comment(self):
        self.assertClean(
            "/* Print an object.\n"
            "   is used to enable certain printing options. The only option\n"
            " */\n"
            "int x = 1;\n"
        )

    def test_url_in_string_literal(self):
        self.assertClean('const char* url = "http://example.com";')


# ---------------------------------------------------------------------------
# Performance / availability
# ---------------------------------------------------------------------------


class TestNoCatastrophicBacktracking(unittest.TestCase):
    """
    The declaration parser previously used a regex with a nested `(?:IDENT\\s*)+?`
    group. On a line of prose it explored every split of the words before
    failing, taking exponential time -- a single comment body line in a real
    header stalled a whole repository scan. Since the server analyses uploaded
    archives, that is an availability risk, not merely a slow path.
    """

    # Generous ceilings: the point is exponential vs. linear, not micro-timing.
    BUDGET_SECONDS = 1.0

    def _timed(self, fn, value):
        start = time.perf_counter()
        fn(value)
        return time.perf_counter() - start

    def test_long_prose_line(self):
        line = " ".join(["word"] * 60)
        self.assertLess(self._timed(parse_variable_declaration, line), self.BUDGET_SECONDS)

    def test_long_prose_line_ending_in_semicolon(self):
        line = " ".join(["word"] * 60) + ";"
        self.assertLess(self._timed(parse_variable_declaration, line), self.BUDGET_SECONDS)

    def test_many_qualifiers(self):
        line = " ".join(["const static volatile unsigned long"] * 20) + " x;"
        self.assertLess(self._timed(parse_variable_declaration, line), self.BUDGET_SECONDS)

    def test_function_declaration_check_is_bounded(self):
        line = "void f(" + ", ".join(["int a"] * 80) + ")"
        self.assertLess(self._timed(is_function_declaration, line), self.BUDGET_SECONDS)

    def test_full_file_of_prose_is_fast(self):
        prose = "\n".join(
            "   this is ordinary documentation prose with several words" for _ in range(400)
        )
        engine = AnalysisEngine()
        start = time.perf_counter()
        engine.analyze_plain("/*\n" + prose + "\n*/\nint x = 1;\n")
        self.assertLess(time.perf_counter() - start, 2.0)


if __name__ == "__main__":
    unittest.main()
