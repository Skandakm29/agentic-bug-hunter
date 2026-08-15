"""
Edit Planner  (Phase 3D.1)
============================
Analyses a confirmed finding, its root cause chain, and the selected context
to produce a structured ``EditPlan`` before any code is generated.

The edit plan answers:
  • Which file(s) need to change?
  • Which function(s) need to change?
  • Is a new helper function needed?
  • Do any header files need updating?
  • Do any #include directives change?
  • Do any API signatures change?
  • What are the expected side effects?

The plan is injected into the LLM prompt so the model generates changes
that are consistent with the repository structure rather than guessing.

Design invariants
-----------------
- One ``EditAction`` per distinct code location that must change.
- Actions are ordered: primary defect site first, then callsites, then headers.
- Cross-file impact is flagged but not required to generate a valid plan.
- The planner is stateless and safe for concurrent use.
"""
from __future__ import annotations

import logging
import re
from typing import List, Optional

from backend.core.analysis.schemas import NormalizedFinding
from backend.core.analysis.root_cause import RootCauseChain
from backend.core.analysis.repo_graph import RepositoryKnowledgeGraph
from backend.core.patch_generation.patch_models import (
    ActionType,
    EditAction,
    EditPlan,
    PatchGenerationConfig,
    RepairCategory,
)

logger = logging.getLogger("backend.patch_generation.edit_planner")


# ---------------------------------------------------------------------------
# Heuristics for deciding which actions to generate
# ---------------------------------------------------------------------------

# Categories where a new helper function is commonly beneficial
_NEW_HELPER_CATEGORIES = {
    RepairCategory.MEMORY_LEAK,
    RepairCategory.RESOURCE_LEAK,
    RepairCategory.MISSING_CLEANUP,
    RepairCategory.INCORRECT_OWNERSHIP,
}

# Categories that may require header updates (signature changes, new types)
_HEADER_UPDATE_CATEGORIES = {
    RepairCategory.CONST_CORRECTNESS,
    RepairCategory.INCORRECT_OWNERSHIP,
    RepairCategory.MISSING_RETURN,
    RepairCategory.EXCEPTION_HANDLING,
}

# Categories where API signature changes are possible
_SIGNATURE_CHANGE_CATEGORIES = {
    RepairCategory.CONST_CORRECTNESS,
    RepairCategory.UNSAFE_CAST,
    RepairCategory.INCORRECT_OWNERSHIP,
}


class EditPlanner:
    """
    Produces a structured ``EditPlan`` for a confirmed finding.

    Parameters
    ----------
    config : ``PatchGenerationConfig`` for behaviour flags
             (e.g. ``allow_new_helpers``).
    """

    def __init__(self, config: PatchGenerationConfig) -> None:
        self._config = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def plan(
        self,
        finding:        NormalizedFinding,
        root_cause:     RootCauseChain,
        repair_category: RepairCategory,
        graph:          Optional[RepositoryKnowledgeGraph] = None,
        bug_id:         str = "",
    ) -> EditPlan:
        """
        Build and return an ``EditPlan`` for the finding.

        Parameters
        ----------
        finding         : The NormalizedFinding to fix.
        root_cause      : Root cause chain from ``RootCauseAnalyzer``.
        repair_category : Classified repair category.
        graph           : Repository knowledge graph for call-graph lookups.
        bug_id          : Opaque bug identifier passed through from the engine.
        """
        ep = EditPlan(
            bug_id=bug_id or finding.rule_id,
            repair_category=repair_category,
        )

        # ── Action 1: primary defect site ─────────────────────────────
        primary_action = self._build_primary_action(finding, repair_category)
        ep.actions.append(primary_action)

        # ── Action 2: root cause site (if different from primary) ─────
        if root_cause.primary:
            rc_action = self._build_root_cause_action(
                finding, root_cause, primary_action, repair_category
            )
            if rc_action:
                ep.actions.append(rc_action)

        # ── Action 3: callers (defensive guard) ───────────────────────
        if graph and finding.file_path:
            caller_actions = self._build_caller_actions(finding, graph, repair_category)
            ep.actions.extend(caller_actions)

        # ── High-level flags ──────────────────────────────────────────
        ep.requires_new_helper = (
            self._config.allow_new_helpers
            and repair_category in _NEW_HELPER_CATEGORIES
        )
        ep.requires_header_update = (
            repair_category in _HEADER_UPDATE_CATEGORIES
        )
        ep.api_changes = (
            repair_category in _SIGNATURE_CHANGE_CATEGORIES
        )
        ep.cross_file_impact = (
            len({a.target_file for a in ep.actions}) > 1
        )

        # ── Expected side effects ─────────────────────────────────────
        ep.expected_side_effects = self._infer_side_effects(
            repair_category, ep
        )

        if ep.requires_new_helper:
            ep.actions.append(EditAction(
                target_file=finding.file_path or "unknown",
                target_function=None,
                action_type=ActionType.ADD_FUNCTION,
                description=(
                    f"Add a helper function to encapsulate the fix pattern "
                    f"for '{repair_category.value}'."
                ),
                estimated_lines=15,
            ))

        logger.info(
            "EditPlanner: %d action(s) for rule '%s' (category=%s, "
            "cross_file=%s, new_helper=%s).",
            len(ep.actions),
            finding.rule_id,
            repair_category.value,
            ep.cross_file_impact,
            ep.requires_new_helper,
        )
        return ep

    # ------------------------------------------------------------------
    # Private builders
    # ------------------------------------------------------------------

    @staticmethod
    def _build_primary_action(
        finding:         NormalizedFinding,
        repair_category: RepairCategory,
    ) -> EditAction:
        """Build the primary edit action at the defect site."""
        func_name = None
        # Heuristic: check if description contains a function name
        m = re.search(r"in function[:\s]+['\"`]?(\w+)['\"`]?", finding.description, re.I)
        if m:
            func_name = m.group(1)

        return EditAction(
            target_file=finding.file_path or "unknown",
            target_function=func_name,
            action_type=ActionType.MODIFY,
            description=(
                f"Fix '{repair_category.value}' at "
                f"L{finding.line_number}: {finding.description}"
            ),
            estimated_lines=max(1, (finding.line_number or 1) % 10 + 1),
            api_signature_changes=(repair_category in _SIGNATURE_CHANGE_CATEGORIES),
        )

    @staticmethod
    def _build_root_cause_action(
        finding:         NormalizedFinding,
        root_cause:      RootCauseChain,
        primary:         EditAction,
        repair_category: RepairCategory,
    ) -> Optional[EditAction]:
        """Build an additional action at the root cause site if different."""
        rc = root_cause.primary
        if not rc:
            return None

        # Extract file:line from hypothesis string  "… at path/file.cpp:42"
        m = re.search(r'(\S+\.\w+):(\d+)', rc.hypothesis)
        if not m:
            return None

        rc_file, rc_line = m.group(1), int(m.group(2))
        if rc_file == primary.target_file:
            # Same file — only add if meaningfully different location
            if abs((finding.line_number or 0) - rc_line) < 3:
                return None

        return EditAction(
            target_file=rc_file,
            action_type=ActionType.MODIFY,
            description=(
                f"Address root cause at {rc_file}:{rc_line} — {rc.hypothesis}"
            ),
            estimated_lines=3,
        )

    @staticmethod
    def _build_caller_actions(
        finding:         NormalizedFinding,
        graph:           RepositoryKnowledgeGraph,
        repair_category: RepairCategory,
    ) -> List[EditAction]:
        """Build defensive guard actions for direct callers of the defect site."""
        # Only for categories where caller guards are appropriate
        guard_categories = {
            RepairCategory.NULL_POINTER_CHECK,
            RepairCategory.BOUNDARY_CONDITION,
            RepairCategory.UNINITIALIZED_VARIABLE,
            RepairCategory.API_MISUSE,
        }
        if repair_category not in guard_categories:
            return []

        file_symbols = [
            s.name for s in graph.symbols.values()
            if s.file_path == finding.file_path and s.symbol_type == "function"
        ]
        actions: List[EditAction] = []
        for sym in file_symbols[:2]:  # cap at 2 callers
            callers = graph.get_callers(sym)
            for caller in callers[:1]:
                caller_sym = graph.symbols.get(caller)
                caller_file = caller_sym.file_path if caller_sym else finding.file_path or "unknown"
                actions.append(EditAction(
                    target_file=caller_file,
                    target_function=caller,
                    action_type=ActionType.MODIFY,
                    description=(
                        f"Add defensive validation guard in caller '{caller}' "
                        f"before invoking '{sym}'."
                    ),
                    estimated_lines=3,
                ))
        return actions

    @staticmethod
    def _infer_side_effects(
        category: RepairCategory,
        plan:     EditPlan,
    ) -> List[str]:
        """Return a list of expected side effects based on category and plan."""
        effects: List[str] = []

        if plan.cross_file_impact:
            effects.append(
                "Changes span multiple files — dependent compilation units may need recompilation."
            )
        if plan.api_changes:
            effects.append(
                "API or function signature changes may require updates at all call sites."
            )
        if plan.requires_new_helper:
            effects.append(
                "A new helper function will be introduced — ensure no name collision in the namespace."
            )
        if plan.requires_header_update:
            effects.append(
                "Header file changes will propagate to all translation units that include it."
            )

        # Category-specific effects
        if category == RepairCategory.THREAD_SAFETY:
            effects.append(
                "Adding synchronisation primitives may reduce throughput — measure under load."
            )
        if category == RepairCategory.MEMORY_LEAK:
            effects.append(
                "Switching from raw pointers to smart pointers changes copy/move semantics."
            )
        if category == RepairCategory.EXCEPTION_HANDLING:
            effects.append(
                "Adding try/catch blocks may affect code size and branch prediction."
            )

        return effects
