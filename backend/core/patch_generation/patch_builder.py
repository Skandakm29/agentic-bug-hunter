"""
Patch Builder  (Phase 3D.1)
==============================
Assembles a complete ``StructuredPatch`` from the individual components
produced by the pipeline (parsed candidates, edit plan, diffs, explanations).

Responsibilities
----------------
1. Select the best candidate index per file patch.
2. Rank candidates by confidence and assign ``preferred_rank``.
3. Discard candidates below the configured minimum confidence threshold.
4. Populate the ``warnings`` list with non-fatal advisory messages.
5. Populate the ``dependencies`` list with symbol/file dependency names.
6. Assemble the final ``StructuredPatch`` with complete generation metadata.

Design invariants
-----------------
- At least one candidate is ALWAYS preserved even if its confidence is below
  the threshold (the builder logs a warning rather than raising).
- Warnings are informational; they do not block patch delivery.
- The builder is stateless and thread-safe.
"""
from __future__ import annotations

import logging
import time
from typing import List, Optional

from backend.core.analysis.schemas import NormalizedFinding
from backend.core.patch_generation.patch_models import (
    EditPlan,
    FilePatch,
    GenerationMetadata,
    PatchCandidate,
    PatchGenerationConfig,
    RepairCategory,
    StructuredPatch,
)

logger = logging.getLogger("backend.patch_generation.patch_builder")


class PatchBuilder:
    """
    Assembles a ``StructuredPatch`` from pipeline components.

    Parameters
    ----------
    config : Configuration for minimum confidence thresholds.
    """

    def __init__(self, config: PatchGenerationConfig) -> None:
        self._config = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(
        self,
        finding:             NormalizedFinding,
        candidates:          List[PatchCandidate],
        edit_plan:           EditPlan,
        repair_category:     RepairCategory,
        generation_metadata: GenerationMetadata,
        root_cause_summary:  str = "",
        evidence_summary:    str = "",
        bug_id:              str = "",
    ) -> StructuredPatch:
        """
        Assemble and return a ``StructuredPatch``.

        Parameters
        ----------
        finding              : Source NormalizedFinding.
        candidates           : Parsed, diff-enriched, explained candidates.
        edit_plan            : Pre-generation edit plan.
        repair_category      : Classified repair category.
        generation_metadata  : Runtime stats from the generation pipeline.
        root_cause_summary   : Human-readable root cause text.
        evidence_summary     : Human-readable evidence text.
        bug_id               : Opaque bug identifier.
        """
        # Filter low-confidence candidates (keep at least one)
        filtered = self._filter_candidates(candidates)

        # Build a single FilePatch for the primary affected file
        file_patch = self._build_file_patch(
            finding, filtered, repair_category
        )

        # Collect warnings
        warnings = self._collect_warnings(
            finding, edit_plan, filtered, repair_category
        )

        # Collect dependencies
        dependencies = self._collect_dependencies(finding, edit_plan)

        patch = StructuredPatch(
            bug_id              = bug_id or finding.rule_id,
            rule_id             = finding.rule_id,
            file_patches        = [file_patch],
            root_cause          = root_cause_summary,
            evidence_summary    = evidence_summary,
            repair_category     = repair_category,
            warnings            = warnings,
            dependencies        = dependencies,
            generation_metadata = generation_metadata,
            edit_plan           = edit_plan,
        )

        logger.info(
            "PatchBuilder: assembled StructuredPatch '%s' — %d candidate(s), "
            "%d warning(s), best_confidence=%.2f.",
            patch.patch_id,
            len(filtered),
            len(warnings),
            filtered[0].confidence if filtered else 0.0,
        )
        return patch

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _filter_candidates(
        self,
        candidates: List[PatchCandidate],
    ) -> List[PatchCandidate]:
        """
        Remove candidates below the minimum confidence threshold.
        Always preserves at least the highest-confidence candidate.
        """
        threshold = self._config.min_candidate_confidence
        filtered = [c for c in candidates if c.confidence >= threshold]

        if not filtered and candidates:
            logger.warning(
                "PatchBuilder: all candidates below confidence threshold %.2f — "
                "keeping highest-confidence candidate as fallback.",
                threshold,
            )
            filtered = [max(candidates, key=lambda c: c.confidence)]

        # Re-rank after filtering
        for rank, c in enumerate(filtered, 1):
            c.preferred_rank = rank

        return filtered

    @staticmethod
    def _build_file_patch(
        finding:         NormalizedFinding,
        candidates:      List[PatchCandidate],
        repair_category: RepairCategory,
    ) -> FilePatch:
        """Build the primary FilePatch from the finding and candidates."""
        best_index = 0  # candidates are sorted by confidence descending

        return FilePatch(
            file_path    = finding.file_path or "unknown",
            function_name= None,   # Will be populated by future symbol resolution
            class_name   = None,
            start_line   = finding.line_number,
            end_line     = finding.line_number,
            bug_type     = repair_category,
            severity     = finding.severity,
            confidence   = finding.static_confidence,
            candidates   = candidates,
            best_candidate_index = best_index,
        )

    def _collect_warnings(
        self,
        finding:         NormalizedFinding,
        edit_plan:       EditPlan,
        candidates:      List[PatchCandidate],
        repair_category: RepairCategory,
    ) -> List[str]:
        """Collect non-fatal advisory messages."""
        warnings: List[str] = []

        if edit_plan.cross_file_impact:
            warnings.append(
                "This patch may require changes to additional files. "
                "Review all affected_components in the edit plan."
            )
        if edit_plan.api_changes:
            warnings.append(
                "API or function signature changes detected. "
                "Audit all call sites before applying this patch."
            )
        if edit_plan.requires_header_update:
            warnings.append(
                "Header file changes may be required to propagate the fix correctly."
            )

        low_style = [c for c in candidates if not c.style_preserved]
        if low_style:
            violations = "; ".join(
                v for c in low_style for v in c.style_violations[:2]
            )
            warnings.append(
                f"Style preservation issues detected in {len(low_style)} "
                f"candidate(s): {violations}"
            )

        # Warn if best candidate confidence is low
        if candidates and candidates[0].confidence < 0.5:
            warnings.append(
                f"Best candidate confidence is {candidates[0].confidence:.2f} "
                f"(< 0.5) — review carefully before applying."
            )

        # Category-specific warnings
        if repair_category == RepairCategory.THREAD_SAFETY:
            warnings.append(
                "Thread safety patches require load testing under concurrent conditions."
            )
        if repair_category == RepairCategory.MEMORY_LEAK:
            warnings.append(
                "Memory leak patches should be verified with Valgrind or ASan."
            )

        return warnings

    @staticmethod
    def _collect_dependencies(
        finding:   NormalizedFinding,
        edit_plan: EditPlan,
    ) -> List[str]:
        """Collect external symbols and files this patch depends on."""
        deps: List[str] = []

        # Files affected by actions (beyond the primary file)
        for action in edit_plan.actions:
            if action.target_file != (finding.file_path or "unknown"):
                dep = f"file:{action.target_file}"
                if dep not in deps:
                    deps.append(dep)

        # Include changes
        for action in edit_plan.actions:
            for inc in action.include_changes:
                dep = f"include:{inc}"
                if dep not in deps:
                    deps.append(dep)

        return deps
