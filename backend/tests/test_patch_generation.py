"""
Comprehensive Unit Tests — Phase 3D.1 Patch Generation Engine
==============================================================
Tests every public module of the patch generation package:
  - exceptions
  - patch_models
  - repair_strategies
  - context_selector
  - edit_planner
  - prompt_builder
  - patch_parser
  - diff_generator
  - syntax_preserver
  - patch_explainer
  - patch_history
  - patch_builder
  - patch_generator (engine integration)

Test philosophy
---------------
- All LLM provider calls are mocked — no real API calls.
- Tests are deterministic: no random seeds, no time-dependent assertions.
- Each test class covers exactly one module.
- Fixtures are minimal (smallest possible inputs that exercise the path).
"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path
from typing import List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

# Inject the project root so imports resolve correctly
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# ── Fixtures ─────────────────────────────────────────────────────────────────

_SAMPLE_CPP = """\
#include <cstdlib>
#include <iostream>

int* allocate(int n) {
    int* ptr = (int*)malloc(n * sizeof(int));
    return ptr;
}

void use_ptr() {
    int* p = nullptr;
    *p = 42;  // null dereference
}
"""

_SAMPLE_PATCH_JSON = """{
  "candidates": [
    {
      "candidate_id": "C1",
      "reasoning": "Add null check before dereference.",
      "original_code": "    *p = 42;  // null dereference",
      "patched_code": "    if (p != nullptr) {\\n        *p = 42;\\n    }",
      "confidence": 0.88,
      "advantages": ["Minimal change", "Follows null-check pattern"],
      "disadvantages": ["Does not handle the null case"],
      "estimated_risk": 0.15,
      "explanation": {
        "why_bug_occurred": "Pointer p is initialised to nullptr and then immediately dereferenced.",
        "why_fix_works": "Adding a null guard prevents the dereference when p is null.",
        "trade_offs": "Simple guard; does not address why p is null.",
        "complexity_impact": "Adds one if-branch, minor cyclomatic complexity increase.",
        "safety_considerations": "Safe for single-threaded code; requires mutex for multi-threaded use.",
        "side_effects": "None.",
        "limitations": "Root cause (why p is null) is not addressed."
      }
    },
    {
      "candidate_id": "C2",
      "reasoning": "Initialise p from allocate() instead of nullptr.",
      "original_code": "    int* p = nullptr;",
      "patched_code": "    int* p = allocate(1);",
      "confidence": 0.72,
      "advantages": ["Addresses root cause"],
      "disadvantages": ["allocate() may also return nullptr"],
      "estimated_risk": 0.30,
      "explanation": {
        "why_bug_occurred": "p was never given a valid address.",
        "why_fix_works": "allocate() provides a valid heap address.",
        "trade_offs": "Requires allocate() to succeed; no free() shown.",
        "complexity_impact": "No change.",
        "safety_considerations": "Must check allocate() return value.",
        "side_effects": "Memory must be freed.",
        "limitations": "Introduces a potential memory leak."
      }
    }
  ]
}"""


def _make_finding(
    rule_id:     str    = "null_pointer",
    file_path:   str    = "src/main.cpp",
    line_number: int    = 11,
    line_text:   str    = "    *p = 42;  // null dereference",
    severity:    str    = "HIGH",
    confidence:  float  = 0.85,
    description: str    = "Null pointer dereference detected.",
    evidence:    str    = "ptr dereferenced without null check",
    remediation: str    = "Add null check before dereference.",
):
    from backend.core.analysis.schemas import NormalizedFinding
    return NormalizedFinding(
        rule_id=rule_id,
        file_path=file_path,
        line_number=line_number,
        line_text=line_text,
        severity=severity,
        static_confidence=confidence,
        description=description,
        evidence=evidence,
        remediation=remediation,
        source="static",
    )


def _make_root_cause(rule_id: str = "null_pointer"):
    from backend.core.analysis.root_cause import CausalNode, RootCauseChain
    chain = RootCauseChain(finding_rule_id=rule_id)
    chain.nodes = [
        CausalNode(
            hypothesis       = "Pointer p is nullptr at dereference site.",
            evidence_sources = ["null_pointer", "p"],
            confidence       = 0.85,
        )
    ]
    return chain


def _make_provider(response: Optional[str] = None) -> MagicMock:
    """Create a mock LLM provider."""
    provider             = MagicMock()
    provider.__class__.__name__ = "GroqProvider"
    provider.generate_completion_async = AsyncMock(
        return_value=response if response is not None else _SAMPLE_PATCH_JSON
    )
    return provider


# =============================================================================
# 1. Exceptions
# =============================================================================

class TestExceptions(unittest.TestCase):

    def test_base_exception(self):
        from backend.core.patch_generation.exceptions import PatchGenerationError
        exc = PatchGenerationError("base error", details={"key": "value"})
        self.assertEqual(exc.message, "base error")
        self.assertEqual(exc.details["key"], "value")
        self.assertIsInstance(exc, RuntimeError)

    def test_provider_unavailable(self):
        from backend.core.patch_generation.exceptions import ProviderUnavailableError
        exc = ProviderUnavailableError(provider="groq")
        self.assertIn("groq", exc.message)
        self.assertEqual(exc.provider, "groq")

    def test_retry_exhausted(self):
        from backend.core.patch_generation.exceptions import RetryExhaustedError
        exc = RetryExhaustedError(attempts=3, provider="openai")
        self.assertEqual(exc.attempts, 3)
        self.assertIn("3", exc.message)

    def test_malformed_patch_output(self):
        from backend.core.patch_generation.exceptions import MalformedPatchOutputError
        exc = MalformedPatchOutputError(raw_response="bad json {{{")
        self.assertIn("bad json", exc.details["raw_response_preview"])

    def test_empty_patch_error(self):
        from backend.core.patch_generation.exceptions import EmptyPatchError
        exc = EmptyPatchError(bug_id="BUG-001", candidate=2)
        self.assertEqual(exc.bug_id, "BUG-001")
        self.assertEqual(exc.candidate, 2)

    def test_context_overflow(self):
        from backend.core.patch_generation.exceptions import ContextOverflowError
        exc = ContextOverflowError(estimated_tokens=10000, budget=4096)
        self.assertEqual(exc.estimated_tokens, 10000)
        self.assertEqual(exc.budget, 4096)

    def test_prompt_overflow(self):
        from backend.core.patch_generation.exceptions import PromptOverflowError
        exc = PromptOverflowError(prompt_tokens=8000, max_tokens=4096)
        self.assertIn("8000", exc.message)

    def test_unsupported_bug_type(self):
        from backend.core.patch_generation.exceptions import UnsupportedBugTypeError
        exc = UnsupportedBugTypeError(bug_type="quantum_entanglement")
        self.assertIn("quantum_entanglement", exc.message)

    def test_missing_context(self):
        from backend.core.patch_generation.exceptions import MissingContextError
        exc = MissingContextError(missing_field="code")
        self.assertIn("code", exc.message)

    def test_inheritance_chain(self):
        from backend.core.patch_generation.exceptions import (
            PatchGenerationError, ProviderUnavailableError
        )
        exc = ProviderUnavailableError("groq")
        self.assertIsInstance(exc, PatchGenerationError)
        self.assertIsInstance(exc, RuntimeError)


# =============================================================================
# 2. Patch Models
# =============================================================================

class TestPatchModels(unittest.TestCase):

    def test_repair_category_enum_count(self):
        from backend.core.patch_generation.patch_models import RepairCategory
        # Should have exactly 21 members (20 spec categories + UNKNOWN)
        self.assertEqual(len(RepairCategory), 21)

    def test_patch_generation_config_defaults(self):
        from backend.core.patch_generation.patch_models import PatchGenerationConfig
        cfg = PatchGenerationConfig()
        self.assertEqual(cfg.candidate_count, 3)
        self.assertEqual(cfg.max_retries, 3)
        self.assertGreater(cfg.context_window, 0)
        self.assertGreater(cfg.max_tokens, 0)

    def test_patch_generation_config_custom(self):
        from backend.core.patch_generation.patch_models import PatchGenerationConfig
        cfg = PatchGenerationConfig(candidate_count=5, temperature=0.5)
        self.assertEqual(cfg.candidate_count, 5)
        self.assertAlmostEqual(cfg.temperature, 0.5)

    def test_patch_candidate_defaults(self):
        from backend.core.patch_generation.patch_models import PatchCandidate
        c = PatchCandidate(original_code="old", patched_code="new")
        self.assertTrue(c.candidate_id)
        self.assertEqual(c.confidence, 0.5)
        self.assertTrue(c.style_preserved)

    def test_structured_patch_defaults(self):
        from backend.core.patch_generation.patch_models import StructuredPatch
        sp = StructuredPatch(bug_id="BUG-001")
        self.assertTrue(sp.patch_id)
        self.assertEqual(sp.bug_id, "BUG-001")
        self.assertEqual(sp.file_patches, [])

    def test_patch_history_entry_serialisation(self):
        from backend.core.patch_generation.patch_models import PatchHistoryEntry, RepairCategory
        entry = PatchHistoryEntry(
            bug_id="BUG-42",
            repair_category=RepairCategory.NULL_POINTER_CHECK,
            provider="groq",
            patch_id="PATCH-1",
            candidate_count=3,
        )
        d = entry.model_dump()
        self.assertEqual(d["bug_id"], "BUG-42")
        self.assertEqual(d["repair_category"], "null_pointer_check")

    def test_edit_plan_serialisation(self):
        from backend.core.patch_generation.patch_models import (
            EditPlan, RepairCategory, EditAction, ActionType
        )
        plan = EditPlan(
            bug_id="BUG-1",
            repair_category=RepairCategory.MEMORY_LEAK,
        )
        plan.actions.append(EditAction(
            target_file="src/main.cpp",
            action_type=ActionType.MODIFY,
            description="Fix memory leak",
        ))
        self.assertEqual(len(plan.actions), 1)
        self.assertFalse(plan.cross_file_impact)


# =============================================================================
# 3. Repair Strategies
# =============================================================================

class TestRepairStrategies(unittest.TestCase):

    def setUp(self):
        from backend.core.patch_generation.repair_strategies import RepairGuidanceRegistry
        self.registry = RepairGuidanceRegistry()

    def test_all_categories_registered(self):
        from backend.core.patch_generation.patch_models import RepairCategory
        for category in RepairCategory:
            guidance = self.registry.get(category)
            self.assertIsNotNone(guidance, f"No guidance for {category}")

    def test_null_pointer_guidance(self):
        from backend.core.patch_generation.patch_models import RepairCategory
        g = self.registry.get(RepairCategory.NULL_POINTER_CHECK)
        self.assertEqual(g.category, RepairCategory.NULL_POINTER_CHECK)
        self.assertTrue(g.repair_approach)
        self.assertTrue(g.safety_notes)
        self.assertGreater(len(g.common_patterns), 0)
        self.assertGreater(len(g.pitfalls), 0)

    def test_unknown_category_fallback(self):
        from backend.core.patch_generation.patch_models import RepairCategory
        g = self.registry.get(RepairCategory.UNKNOWN)
        self.assertIsNotNone(g)
        self.assertEqual(g.category, RepairCategory.UNKNOWN)

    def test_custom_registration(self):
        from backend.core.patch_generation.patch_models import RepairCategory
        from backend.core.patch_generation.repair_strategies import RepairGuidance
        custom = RepairGuidance(
            category=RepairCategory.THREAD_SAFETY,
            description="Custom thread safety guidance.",
            repair_approach="Lock all the things.",
        )
        self.registry.register(custom)
        g = self.registry.get(RepairCategory.THREAD_SAFETY)
        self.assertEqual(g.description, "Custom thread safety guidance.")

    def test_list_categories(self):
        cats = self.registry.list_categories()
        self.assertGreater(len(cats), 0)

    def test_has_guidance(self):
        from backend.core.patch_generation.patch_models import RepairCategory
        self.assertTrue(self.registry.has_guidance(RepairCategory.MEMORY_LEAK))

    def test_example_fix_present_for_memory_leak(self):
        from backend.core.patch_generation.patch_models import RepairCategory
        g = self.registry.get(RepairCategory.MEMORY_LEAK)
        self.assertTrue(g.example_fix)


# =============================================================================
# 4. Context Selector
# =============================================================================

class TestContextSelector(unittest.TestCase):

    def setUp(self):
        from backend.core.patch_generation.patch_models import PatchGenerationConfig
        from backend.core.patch_generation.context_selector import ContextSelector
        self.config   = PatchGenerationConfig(context_window=5000)
        self.selector = ContextSelector(self.config)
        self.finding  = _make_finding()

    def test_basic_context_selection(self):
        ctx = self.selector.select(self.finding, _SAMPLE_CPP)
        self.assertIsNotNone(ctx.target_file_content)
        self.assertGreater(len(ctx.target_file_content), 0)

    def test_target_region_includes_bug_line(self):
        ctx = self.selector.select(self.finding, _SAMPLE_CPP)
        self.assertIn("*p = 42", ctx.target_file_content)

    def test_includes_extracted(self):
        ctx = self.selector.select(self.finding, _SAMPLE_CPP)
        # #include <cstdlib> and #include <iostream> should be found
        self.assertGreater(len(ctx.required_includes), 0)
        self.assertTrue(any("cstdlib" in inc for inc in ctx.required_includes))

    def test_estimated_char_count(self):
        ctx = self.selector.select(self.finding, _SAMPLE_CPP)
        self.assertGreater(ctx.estimated_char_count, 0)

    def test_no_line_number_returns_full_file(self):
        finding_no_line = _make_finding(line_number=None)
        # Temporarily set line_number via dict trick since Pydantic v2 allows mutation
        ctx = self.selector.select(finding_no_line, _SAMPLE_CPP)
        self.assertGreater(len(ctx.target_file_content), 0)

    def test_context_overflow_raises_for_tiny_budget(self):
        from backend.core.patch_generation.patch_models import PatchGenerationConfig
        from backend.core.patch_generation.context_selector import ContextSelector
        from backend.core.patch_generation.exceptions import ContextOverflowError
        tiny_config   = PatchGenerationConfig(context_window=512)
        tiny_selector = ContextSelector(tiny_config)
        long_code = ("// A very long line to exceed character budget " * 20 + "\n") * 100
        with self.assertRaises(ContextOverflowError):
            tiny_selector.select(self.finding, long_code)

    def test_rag_documents_extracted_from_evidence(self):
        from backend.core.analysis.evidence import EvidenceGraph, EvidenceNode, EvidenceNodeType
        evidence = EvidenceGraph(finding_id="test")
        doc_node = EvidenceNode(
            node_type=EvidenceNodeType.DOCUMENTATION,
            label="RAII docs",
            metadata={"text": "Use RAII to prevent leaks."},
        )
        evidence.add_node(doc_node)
        ctx = self.selector.select(self.finding, _SAMPLE_CPP, evidence=evidence)
        self.assertGreater(len(ctx.rag_documents), 0)


# =============================================================================
# 5. Edit Planner
# =============================================================================

class TestEditPlanner(unittest.TestCase):

    def setUp(self):
        from backend.core.patch_generation.patch_models import PatchGenerationConfig
        from backend.core.patch_generation.edit_planner import EditPlanner
        self.planner = EditPlanner(PatchGenerationConfig())

    def test_basic_plan_creation(self):
        from backend.core.patch_generation.patch_models import RepairCategory
        finding    = _make_finding()
        root_cause = _make_root_cause()
        plan = self.planner.plan(
            finding, root_cause, RepairCategory.NULL_POINTER_CHECK
        )
        self.assertIsNotNone(plan)
        self.assertGreater(len(plan.actions), 0)

    def test_primary_action_targets_correct_file(self):
        from backend.core.patch_generation.patch_models import RepairCategory
        plan = self.planner.plan(
            _make_finding(file_path="src/foo.cpp"),
            _make_root_cause(),
            RepairCategory.NULL_POINTER_CHECK,
        )
        self.assertEqual(plan.actions[0].target_file, "src/foo.cpp")

    def test_new_helper_for_memory_leak(self):
        from backend.core.patch_generation.patch_models import RepairCategory, PatchGenerationConfig
        from backend.core.patch_generation.edit_planner import EditPlanner
        cfg     = PatchGenerationConfig(allow_new_helpers=True)
        planner = EditPlanner(cfg)
        plan = planner.plan(
            _make_finding(), _make_root_cause(), RepairCategory.MEMORY_LEAK
        )
        self.assertTrue(plan.requires_new_helper)

    def test_no_new_helper_when_disabled(self):
        from backend.core.patch_generation.patch_models import RepairCategory, PatchGenerationConfig
        from backend.core.patch_generation.edit_planner import EditPlanner
        cfg     = PatchGenerationConfig(allow_new_helpers=False)
        planner = EditPlanner(cfg)
        plan = planner.plan(
            _make_finding(), _make_root_cause(), RepairCategory.MEMORY_LEAK
        )
        self.assertFalse(plan.requires_new_helper)

    def test_side_effects_populated(self):
        from backend.core.patch_generation.patch_models import RepairCategory
        plan = self.planner.plan(
            _make_finding(), _make_root_cause(), RepairCategory.THREAD_SAFETY
        )
        self.assertTrue(any("thread" in e.lower() or "synchronisation" in e.lower()
                            for e in plan.expected_side_effects))

    def test_bug_id_propagated(self):
        from backend.core.patch_generation.patch_models import RepairCategory
        plan = self.planner.plan(
            _make_finding(), _make_root_cause(),
            RepairCategory.NULL_POINTER_CHECK,
            bug_id="CUSTOM-BUG-42",
        )
        self.assertEqual(plan.bug_id, "CUSTOM-BUG-42")


# =============================================================================
# 6. Prompt Builder
# =============================================================================

class TestPromptBuilder(unittest.TestCase):

    def setUp(self):
        from backend.core.patch_generation.patch_models import (
            PatchGenerationConfig, ContextWindow, EditPlan, RepairCategory,
        )
        from backend.core.patch_generation.prompt_builder import PatchPromptBuilder
        self.config  = PatchGenerationConfig(candidate_count=2, max_tokens=8192)
        self.builder = PatchPromptBuilder(self.config)
        self.finding = _make_finding()
        self.rc      = _make_root_cause()
        self.ctx     = ContextWindow(
            target_file_content=_SAMPLE_CPP,
            required_includes=["#include <cstdlib>"],
        )
        self.plan = EditPlan(
            bug_id="BUG-1",
            repair_category=RepairCategory.NULL_POINTER_CHECK,
        )

    def test_system_prompt_not_empty(self):
        sp = self.builder.build_system_prompt()
        self.assertGreater(len(sp), 100)
        self.assertIn("Principal Software Engineer", sp)

    def test_system_prompt_contains_security_guard(self):
        sp = self.builder.build_system_prompt()
        self.assertIn("SECURITY", sp)

    def test_user_prompt_contains_bug_summary(self):
        from backend.core.patch_generation.patch_models import RepairCategory
        up = self.builder.build_user_prompt(
            self.finding, self.rc, self.ctx, self.plan,
            RepairCategory.NULL_POINTER_CHECK,
        )
        self.assertIn("null_pointer", up)
        self.assertIn("HIGH", up)

    def test_user_prompt_contains_output_schema(self):
        from backend.core.patch_generation.patch_models import RepairCategory
        up = self.builder.build_user_prompt(
            self.finding, self.rc, self.ctx, self.plan,
            RepairCategory.NULL_POINTER_CHECK,
        )
        self.assertIn("candidates", up)
        self.assertIn("patched_code", up)

    def test_user_prompt_contains_repair_guidance(self):
        from backend.core.patch_generation.patch_models import RepairCategory
        up = self.builder.build_user_prompt(
            self.finding, self.rc, self.ctx, self.plan,
            RepairCategory.NULL_POINTER_CHECK,
        )
        self.assertIn("null_pointer_check", up.lower())

    def test_prompt_overflow_raises(self):
        from backend.core.patch_generation.patch_models import (
            PatchGenerationConfig, RepairCategory
        )
        from backend.core.patch_generation.prompt_builder import PatchPromptBuilder
        from backend.core.patch_generation.exceptions import PromptOverflowError
        # The prompt is measured against the *input* budget.
        tiny_config  = PatchGenerationConfig(max_prompt_tokens=512)
        tiny_builder = PatchPromptBuilder(tiny_config)
        with self.assertRaises(PromptOverflowError):
            tiny_builder.build_user_prompt(
                self.finding, self.rc, self.ctx, self.plan,
                RepairCategory.NULL_POINTER_CHECK,
            )

    def test_small_output_budget_does_not_trigger_prompt_overflow(self):
        """max_tokens bounds the completion, not the prompt we send."""
        from backend.core.patch_generation.patch_models import (
            PatchGenerationConfig, RepairCategory
        )
        from backend.core.patch_generation.prompt_builder import PatchPromptBuilder
        cfg     = PatchGenerationConfig(max_tokens=128, max_prompt_tokens=12000)
        builder = PatchPromptBuilder(cfg)
        prompt  = builder.build_user_prompt(
            self.finding, self.rc, self.ctx, self.plan,
            RepairCategory.NULL_POINTER_CHECK,
        )
        self.assertIn("BUG REPORT", prompt)

    def test_candidate_count_in_output_schema(self):
        from backend.core.patch_generation.patch_models import (
            PatchGenerationConfig, RepairCategory
        )
        from backend.core.patch_generation.prompt_builder import PatchPromptBuilder
        cfg     = PatchGenerationConfig(candidate_count=5)
        builder = PatchPromptBuilder(cfg)
        up = builder.build_user_prompt(
            self.finding, self.rc, self.ctx, self.plan,
            RepairCategory.NULL_POINTER_CHECK,
        )
        self.assertIn("5", up)

    def test_estimate_tokens(self):
        tokens = self.builder.estimate_tokens("A" * 400)
        self.assertEqual(tokens, 100)  # 400 // 4


# =============================================================================
# 7. Patch Parser
# =============================================================================

class TestPatchParser(unittest.TestCase):

    def setUp(self):
        from backend.core.patch_generation.patch_parser import PatchOutputParser
        self.parser = PatchOutputParser()

    def test_parse_valid_json(self):
        candidates = self.parser.parse(_SAMPLE_PATCH_JSON, bug_id="BUG-1")
        self.assertEqual(len(candidates), 2)

    def test_candidates_sorted_by_confidence(self):
        candidates = self.parser.parse(_SAMPLE_PATCH_JSON, bug_id="BUG-1")
        self.assertGreater(candidates[0].confidence, candidates[1].confidence)

    def test_preferred_rank_assigned(self):
        candidates = self.parser.parse(_SAMPLE_PATCH_JSON, bug_id="BUG-1")
        self.assertEqual(candidates[0].preferred_rank, 1)
        self.assertEqual(candidates[1].preferred_rank, 2)

    def test_explanation_parsed(self):
        candidates = self.parser.parse(_SAMPLE_PATCH_JSON, bug_id="BUG-1")
        self.assertIsNotNone(candidates[0].explanation)
        self.assertIn("nullptr", candidates[0].explanation.why_bug_occurred)

    def test_parse_json_inside_markdown_fence(self):
        fenced = f"Here is the fix:\n```json\n{_SAMPLE_PATCH_JSON}\n```"
        candidates = self.parser.parse(fenced, bug_id="BUG-1")
        self.assertGreater(len(candidates), 0)

    def test_parse_json_with_leading_text(self):
        leading = f"Sure! Here is my response:\n{_SAMPLE_PATCH_JSON}"
        candidates = self.parser.parse(leading, bug_id="BUG-1")
        self.assertGreater(len(candidates), 0)

    def test_parse_empty_response_raises(self):
        from backend.core.patch_generation.exceptions import MalformedPatchOutputError
        with self.assertRaises(MalformedPatchOutputError):
            self.parser.parse("", bug_id="BUG-1")

    def test_parse_invalid_json_raises(self):
        from backend.core.patch_generation.exceptions import MalformedPatchOutputError
        with self.assertRaises(MalformedPatchOutputError):
            self.parser.parse("not json at all {{{", bug_id="BUG-1")

    def test_parse_empty_patch_raises(self):
        from backend.core.patch_generation.exceptions import EmptyPatchError
        empty_json = '{"candidates": [{"original_code": "x = 1;", "patched_code": "x = 1;", "confidence": 0.8}]}'
        with self.assertRaises(EmptyPatchError):
            self.parser.parse(empty_json, bug_id="BUG-1")

    def test_parse_single_candidate_top_level(self):
        single = '{"original_code": "*p = 42;", "patched_code": "if (p) *p = 42;", "confidence": 0.7}'
        candidates = self.parser.parse(single, bug_id="BUG-1")
        self.assertEqual(len(candidates), 1)

    def test_confidence_clamped_to_range(self):
        clamp_json = '{"candidates": [{"original_code": "x;", "patched_code": "y;", "confidence": 9.9}]}'
        candidates = self.parser.parse(clamp_json, bug_id="BUG-1")
        self.assertLessEqual(candidates[0].confidence, 1.0)

    def test_missing_original_code_skipped(self):
        partial = '{"candidates": [{"original_code": "", "patched_code": "fix;", "confidence": 0.8}, {"original_code": "old;", "patched_code": "new;", "confidence": 0.7}]}'
        candidates = self.parser.parse(partial, bug_id="BUG-1")
        self.assertEqual(len(candidates), 1)


# =============================================================================
# 8. Diff Generator
# =============================================================================

class TestDiffGenerator(unittest.TestCase):

    def setUp(self):
        from backend.core.patch_generation.diff_generator import UnifiedDiffGenerator
        self.gen = UnifiedDiffGenerator()

    def test_generates_unified_diff(self):
        diff = self.gen.generate(
            "    *p = 42;\n",
            "    if (p) *p = 42;\n",
            file_path="src/main.cpp",
        )
        self.assertIn("---", diff)
        self.assertIn("+++", diff)
        self.assertIn("@@", diff)

    def test_empty_diff_for_identical_code(self):
        diff = self.gen.generate("same code\n", "same code\n")
        self.assertEqual(diff, "")

    def test_file_path_in_headers(self):
        diff = self.gen.generate(
            "old\n", "new\n", file_path="include/foo.hpp"
        )
        self.assertIn("a/include/foo.hpp", diff)
        self.assertIn("b/include/foo.hpp", diff)

    def test_generate_from_candidates(self):
        from backend.core.patch_generation.patch_models import PatchCandidate
        candidates = [
            PatchCandidate(original_code="old line\n", patched_code="new line\n"),
            PatchCandidate(original_code="same\n", patched_code="same\n"),
        ]
        result = self.gen.generate_from_candidates(
            candidates, file_path="src/main.cpp"
        )
        self.assertNotEqual(result[0].unified_diff, "")
        self.assertEqual(result[1].unified_diff, "")

    def test_crlf_normalised(self):
        diff = self.gen.generate("old\r\n", "new\r\n", file_path="foo.cpp")
        self.assertNotIn("\r\n---", diff)

    def test_context_lines_respected(self):
        from backend.core.patch_generation.diff_generator import UnifiedDiffGenerator
        gen = UnifiedDiffGenerator(context_lines=0)
        orig = "line1\nline2\nTARGET\nline4\nline5\n"
        patched = "line1\nline2\nFIXED\nline4\nline5\n"
        diff = gen.generate(orig, patched)
        # With 0 context lines, line1/line2/line4/line5 should NOT appear in hunks
        lines = [l for l in diff.split('\n') if l.startswith(' ')]
        self.assertEqual(len(lines), 0)

    def test_summary_diff_contains_all_candidates(self):
        from backend.core.patch_generation.patch_models import PatchCandidate
        candidates = [
            PatchCandidate(original_code="a\n", patched_code="b\n", preferred_rank=1, confidence=0.9),
            PatchCandidate(original_code="c\n", patched_code="d\n", preferred_rank=2, confidence=0.7),
        ]
        summary = self.gen.generate_summary_diff(candidates)
        self.assertIn("Candidate 1", summary)
        self.assertIn("Candidate 2", summary)


# =============================================================================
# 9. Syntax Preserver
# =============================================================================

class TestSyntaxPreserver(unittest.TestCase):

    def setUp(self):
        from backend.core.patch_generation.syntax_preserver import SyntaxPreserver
        self.preserver = SyntaxPreserver()

    def test_detects_spaces_over_tabs(self):
        code   = "    int x = 1;\n    int y = 2;\n"
        profile = self.preserver.analyze_style(code)
        self.assertFalse(profile.uses_tabs)

    def test_detects_tabs(self):
        code   = "\tint x = 1;\n\tint y = 2;\n\tint z = 3;\n" * 5
        profile = self.preserver.analyze_style(code)
        self.assertTrue(profile.uses_tabs)

    def test_detects_kr_brace_style(self):
        code = (
            "void foo() {\n"
            "    if (x) {\n"
            "        return;\n"
            "    }\n"
        ) * 5
        profile = self.preserver.analyze_style(code)
        self.assertEqual(profile.brace_style, "kr")

    def test_detects_allman_brace_style(self):
        code = (
            "void foo()\n"
            "{\n"
            "    if (x)\n"
            "    {\n"
            "        return;\n"
            "    }\n"
        ) * 5
        profile = self.preserver.analyze_style(code)
        self.assertEqual(profile.brace_style, "allman")

    def test_detect_line_comment_style(self):
        code = "// this is a comment\nint x = 1; // inline\n" * 5
        profile = self.preserver.analyze_style(code)
        self.assertEqual(profile.comment_style, "line")

    def test_style_profile_to_prompt_summary(self):
        code    = "    int x = 1;\n"
        profile = self.preserver.analyze_style(code)
        summary = profile.to_prompt_summary()
        self.assertIn("Indentation", summary)
        self.assertIn("Brace style", summary)

    def test_short_source_returns_defaults(self):
        profile = self.preserver.analyze_style("int x;")
        self.assertIsNotNone(profile)
        self.assertEqual(profile.indent_width, 4)

    def test_validate_no_violations(self):
        from backend.core.patch_generation.patch_models import PatchCandidate
        from backend.core.patch_generation.syntax_preserver import StyleProfile
        candidates = [
            PatchCandidate(
                original_code="    int x = 1;\n",
                patched_code="    int x = 1;\n    int y = 2;\n",
            )
        ]
        profile = StyleProfile()
        result  = self.preserver.validate_preservation(candidates, profile)
        self.assertTrue(result[0].style_preserved)

    def test_validate_detects_indentation_change(self):
        from backend.core.patch_generation.patch_models import PatchCandidate
        from backend.core.patch_generation.syntax_preserver import StyleProfile
        candidates = [
            PatchCandidate(
                original_code="    int x = 1;\n",
                patched_code="\tint x = 1;\n",
            )
        ]
        profile = StyleProfile(uses_tabs=False)
        result  = self.preserver.validate_preservation(candidates, profile)
        self.assertFalse(result[0].style_preserved)
        self.assertGreater(len(result[0].style_violations), 0)


# =============================================================================
# 10. Patch Explainer
# =============================================================================

class TestPatchExplainer(unittest.TestCase):

    def setUp(self):
        from backend.core.patch_generation.patch_models import PatchGenerationConfig
        from backend.core.patch_generation.patch_explainer import PatchExplainerEngine
        self.explainer = PatchExplainerEngine(PatchGenerationConfig())
        self.finding   = _make_finding()
        self.root_cause= _make_root_cause()

    def test_fallback_explanation_generated(self):
        from backend.core.patch_generation.patch_models import PatchCandidate, RepairCategory
        candidates = [PatchCandidate(original_code="*p = 42;", patched_code="if (p) *p = 42;")]
        result = self.explainer.explain_candidates(
            candidates, self.finding, self.root_cause, RepairCategory.NULL_POINTER_CHECK
        )
        self.assertIsNotNone(result[0].explanation)
        self.assertTrue(result[0].explanation.why_bug_occurred)
        self.assertTrue(result[0].explanation.why_fix_works)

    def test_existing_complete_explanation_preserved(self):
        from backend.core.patch_generation.patch_models import (
            PatchCandidate, PatchExplanation, RepairCategory
        )
        existing = PatchExplanation(
            why_bug_occurred      = "The pointer was null from the start.",
            why_fix_works         = "Guard prevents dereference.",
            trade_offs            = "None.",
            complexity_impact     = "Minimal.",
            safety_considerations = "Thread safe.",
            side_effects          = "None.",
            limitations           = "None.",
        )
        candidates = [
            PatchCandidate(
                original_code="*p = 42;",
                patched_code="if (p) *p = 42;",
                explanation=existing,
            )
        ]
        result = self.explainer.explain_candidates(
            candidates, self.finding, self.root_cause, RepairCategory.NULL_POINTER_CHECK
        )
        # The original explanation should be preserved unchanged
        self.assertEqual(result[0].explanation.why_bug_occurred, existing.why_bug_occurred)

    def test_all_fields_populated(self):
        from backend.core.patch_generation.patch_models import PatchCandidate, RepairCategory
        candidates = [PatchCandidate(original_code="*p;", patched_code="if(p)*p;")]
        result = self.explainer.explain_candidates(
            candidates, self.finding, self.root_cause, RepairCategory.NULL_POINTER_CHECK
        )
        exp = result[0].explanation
        for field_name in [
            "why_bug_occurred", "why_fix_works", "trade_offs",
            "complexity_impact", "safety_considerations",
            "side_effects", "limitations"
        ]:
            self.assertTrue(getattr(exp, field_name), f"{field_name} is empty")


# =============================================================================
# 11. Patch History
# =============================================================================

class TestPatchHistory(unittest.TestCase):

    def setUp(self):
        from backend.core.patch_generation.patch_history import PatchHistoryStore
        from backend.core.patch_generation.patch_models import (
            PatchHistoryEntry, RepairCategory
        )
        self.store = PatchHistoryStore()
        self.entry = PatchHistoryEntry(
            bug_id="BUG-1",
            repair_category=RepairCategory.NULL_POINTER_CHECK,
            provider="groq",
            patch_id="PATCH-1",
            candidate_count=3,
        )

    def test_record_and_retrieve(self):
        self.store.record(self.entry)
        results = self.store.get_by_bug_id("BUG-1")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].bug_id, "BUG-1")

    def test_get_recent(self):
        from backend.core.patch_generation.patch_models import PatchHistoryEntry, RepairCategory
        for i in range(5):
            e = PatchHistoryEntry(
                bug_id=f"BUG-{i}",
                repair_category=RepairCategory.NULL_POINTER_CHECK,
                provider="groq",
                patch_id=f"PATCH-{i}",
                candidate_count=2,
            )
            self.store.record(e)
        recent = self.store.get_recent(3)
        self.assertEqual(len(recent), 3)
        # Most recent should be BUG-4
        self.assertEqual(recent[0].bug_id, "BUG-4")

    def test_get_by_category(self):
        from backend.core.patch_generation.patch_models import (
            PatchHistoryEntry, RepairCategory
        )
        self.store.record(self.entry)
        ml_entry = PatchHistoryEntry(
            bug_id="BUG-2",
            repair_category=RepairCategory.MEMORY_LEAK,
            provider="groq",
            patch_id="PATCH-2",
            candidate_count=2,
        )
        self.store.record(ml_entry)
        results = self.store.get_by_category(RepairCategory.NULL_POINTER_CHECK)
        self.assertEqual(len(results), 1)

    def test_mark_accepted(self):
        self.store.record(self.entry)
        success = self.store.mark_accepted(self.entry.entry_id, "CANDIDATE-1")
        self.assertTrue(success)
        results = self.store.get_accepted()
        self.assertEqual(len(results), 1)

    def test_mark_accepted_unknown_id(self):
        result = self.store.mark_accepted("nonexistent-id", "C1")
        self.assertFalse(result)

    def test_export_import_roundtrip(self):
        self.store.record(self.entry)
        exported = self.store.export()
        from backend.core.patch_generation.patch_history import PatchHistoryStore
        restored = PatchHistoryStore.import_from_json(exported)
        self.assertEqual(len(restored), 1)
        self.assertEqual(restored.all()[0].bug_id, "BUG-1")

    def test_stats(self):
        self.store.record(self.entry)
        stats = self.store.stats()
        self.assertEqual(stats["total"], 1)
        self.assertEqual(stats["accepted_count"], 0)
        self.assertIn("null_pointer_check", stats["by_category"])

    def test_clear(self):
        self.store.record(self.entry)
        self.store.clear()
        self.assertEqual(len(self.store), 0)

    def test_len(self):
        self.store.record(self.entry)
        self.assertEqual(len(self.store), 1)


# =============================================================================
# 12. Patch Builder
# =============================================================================

class TestPatchBuilder(unittest.TestCase):

    def setUp(self):
        from backend.core.patch_generation.patch_models import (
            PatchGenerationConfig, PatchCandidate, EditPlan, RepairCategory,
            GenerationMetadata
        )
        from backend.core.patch_generation.patch_builder import PatchBuilder
        self.config  = PatchGenerationConfig()
        self.builder = PatchBuilder(self.config)
        self.finding = _make_finding()

        self.candidates = [
            PatchCandidate(
                original_code="*p = 42;",
                patched_code="if (p) *p = 42;",
                confidence=0.88,
                estimated_risk=0.15,
            ),
            PatchCandidate(
                original_code="*p = 42;",
                patched_code="if (p != nullptr) { *p = 42; }",
                confidence=0.72,
                estimated_risk=0.20,
            ),
        ]

        self.edit_plan = EditPlan(
            bug_id="BUG-1",
            repair_category=RepairCategory.NULL_POINTER_CHECK,
        )
        self.gen_meta = GenerationMetadata(provider="groq", generation_time_ms=1200.0)

    def test_builds_structured_patch(self):
        from backend.core.patch_generation.patch_models import RepairCategory
        patch = self.builder.build(
            finding=self.finding,
            candidates=self.candidates,
            edit_plan=self.edit_plan,
            repair_category=RepairCategory.NULL_POINTER_CHECK,
            generation_metadata=self.gen_meta,
            bug_id="BUG-1",
        )
        self.assertIsNotNone(patch)
        self.assertEqual(patch.bug_id, "BUG-1")
        self.assertEqual(len(patch.file_patches), 1)

    def test_best_candidate_is_highest_confidence(self):
        from backend.core.patch_generation.patch_models import RepairCategory
        patch = self.builder.build(
            self.finding, self.candidates, self.edit_plan,
            RepairCategory.NULL_POINTER_CHECK, self.gen_meta,
        )
        # candidates are pre-sorted by confidence; best_candidate_index=0
        fp = patch.file_patches[0]
        self.assertEqual(fp.best_candidate_index, 0)

    def test_low_confidence_candidate_kept_as_fallback(self):
        from backend.core.patch_generation.patch_models import (
            RepairCategory, PatchCandidate, PatchGenerationConfig
        )
        from backend.core.patch_generation.patch_builder import PatchBuilder
        cfg     = PatchGenerationConfig(min_candidate_confidence=0.99)
        builder = PatchBuilder(cfg)
        low_conf = [
            PatchCandidate(original_code="x;", patched_code="y;", confidence=0.1)
        ]
        patch = builder.build(
            self.finding, low_conf, self.edit_plan,
            RepairCategory.NULL_POINTER_CHECK, self.gen_meta,
        )
        # Even with threshold=0.99, at least one candidate kept
        self.assertEqual(len(patch.file_patches[0].candidates), 1)
        self.assertIn("confidence is 0.10", " ".join(patch.warnings).lower())

    def test_warnings_populated_for_thread_safety(self):
        from backend.core.patch_generation.patch_models import RepairCategory
        patch = self.builder.build(
            self.finding, self.candidates, self.edit_plan,
            RepairCategory.THREAD_SAFETY, self.gen_meta,
        )
        self.assertTrue(any("thread" in w.lower() or "concurrent" in w.lower()
                            for w in patch.warnings))

    def test_generation_metadata_preserved(self):
        from backend.core.patch_generation.patch_models import RepairCategory
        patch = self.builder.build(
            self.finding, self.candidates, self.edit_plan,
            RepairCategory.NULL_POINTER_CHECK, self.gen_meta,
        )
        self.assertEqual(patch.generation_metadata.provider, "groq")
        self.assertAlmostEqual(patch.generation_metadata.generation_time_ms, 1200.0)


# =============================================================================
# 13. Engine Integration (PatchGenerationEngine)
# =============================================================================

class TestPatchGenerationEngine(unittest.TestCase):
    """Integration tests for the full engine pipeline using a mocked provider."""

    def setUp(self):
        from backend.core.patch_generation.patch_models import PatchGenerationConfig
        from backend.core.patch_generation.patch_generator import PatchGenerationEngine
        self.config   = PatchGenerationConfig(
            candidate_count=2,
            max_retries=1,
            context_window=10000,
        )
        self.provider = _make_provider()
        self.engine   = PatchGenerationEngine(
            provider=self.provider,
            config=self.config,
        )
        self.finding  = _make_finding()
        self.rc       = _make_root_cause()

    def _run(self, coro):
        return asyncio.run(coro)

    def test_full_pipeline_returns_structured_patch(self):
        patch = self._run(self.engine.generate(
            finding=self.finding,
            code=_SAMPLE_CPP,
            root_cause=self.rc,
            bug_id="BUG-001",
        ))
        self.assertIsNotNone(patch)
        self.assertEqual(patch.bug_id, "BUG-001")
        self.assertGreater(len(patch.file_patches), 0)

    def test_patch_contains_candidates(self):
        patch = self._run(self.engine.generate(
            finding=self.finding,
            code=_SAMPLE_CPP,
            root_cause=self.rc,
        ))
        fp = patch.file_patches[0]
        self.assertGreater(len(fp.candidates), 0)

    def test_patch_candidates_have_diffs(self):
        patch = self._run(self.engine.generate(
            finding=self.finding,
            code=_SAMPLE_CPP,
            root_cause=self.rc,
        ))
        for fp in patch.file_patches:
            for c in fp.candidates:
                # At least the first candidate should have a diff
                # (second may be empty if original == patched)
                if c.preferred_rank == 1:
                    self.assertIsInstance(c.unified_diff, str)

    def test_generation_metadata_populated(self):
        patch = self._run(self.engine.generate(
            finding=self.finding,
            code=_SAMPLE_CPP,
        ))
        self.assertGreater(patch.generation_metadata.generation_time_ms, 0)

    def test_history_recorded(self):
        from backend.core.patch_generation.patch_history import PatchHistoryStore
        history = PatchHistoryStore()
        from backend.core.patch_generation.patch_generator import PatchGenerationEngine
        engine = PatchGenerationEngine(
            provider=self.provider,
            config=self.config,
            history=history,
        )
        self._run(engine.generate(self.finding, _SAMPLE_CPP, bug_id="BUG-H"))
        self.assertEqual(len(history), 1)
        self.assertEqual(history.all()[0].bug_id, "BUG-H")

    def test_missing_code_raises(self):
        from backend.core.patch_generation.exceptions import MissingContextError
        with self.assertRaises(MissingContextError):
            self._run(self.engine.generate(self.finding, ""))

    def test_retry_on_provider_none_response(self):
        from backend.core.patch_generation.patch_models import PatchGenerationConfig
        from backend.core.patch_generation.patch_generator import PatchGenerationEngine
        from backend.core.patch_generation.exceptions import RetryExhaustedError

        failing_provider = _make_provider(response="")
        engine = PatchGenerationEngine(
            provider=failing_provider,
            config=PatchGenerationConfig(max_retries=1),
        )
        with self.assertRaises(RetryExhaustedError):
            self._run(engine.generate(self.finding, _SAMPLE_CPP))

    def test_provider_called_once_on_success(self):
        self._run(self.engine.generate(self.finding, _SAMPLE_CPP))
        self.provider.generate_completion_async.assert_called_once()

    def test_category_classified_correctly(self):
        from backend.core.patch_generation.patch_models import RepairCategory
        from backend.core.patch_generation.patch_generator import _classify_category
        result = _classify_category(_make_finding(rule_id="null_pointer"))
        self.assertEqual(result, RepairCategory.NULL_POINTER_CHECK)

    def test_category_unknown_for_unrecognised_rule(self):
        from backend.core.patch_generation.patch_models import RepairCategory
        from backend.core.patch_generation.patch_generator import _classify_category
        result = _classify_category(_make_finding(rule_id="xyz_weird_rule", description=""))
        self.assertEqual(result, RepairCategory.UNKNOWN)


# =============================================================================
# 14. Configuration validation
# =============================================================================

class TestConfiguration(unittest.TestCase):

    def test_default_config_is_valid(self):
        from backend.core.patch_generation.patch_models import PatchGenerationConfig
        cfg = PatchGenerationConfig()
        self.assertGreater(cfg.temperature, 0.0)
        self.assertLessEqual(cfg.temperature, 2.0)
        self.assertGreater(cfg.candidate_count, 0)
        self.assertLessEqual(cfg.candidate_count, 5)

    def test_custom_temperature(self):
        from backend.core.patch_generation.patch_models import PatchGenerationConfig
        cfg = PatchGenerationConfig(temperature=0.0)
        self.assertEqual(cfg.temperature, 0.0)

    def test_repair_style_default(self):
        from backend.core.patch_generation.patch_models import PatchGenerationConfig, RepairStyle
        cfg = PatchGenerationConfig()
        self.assertEqual(cfg.repair_style, RepairStyle.CONSERVATIVE)

    def test_reasoning_mode_default(self):
        from backend.core.patch_generation.patch_models import PatchGenerationConfig, ReasoningMode
        cfg = PatchGenerationConfig()
        self.assertEqual(cfg.reasoning_mode, ReasoningMode.STRUCTURED)


if __name__ == "__main__":
    unittest.main(verbosity=2)
