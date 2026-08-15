"""
Patch Planning Engine  (§121)
================================
Plans minimal, evidence-backed fix strategies for confirmed findings without
generating implementation code.

Design invariants
-----------------
- Generates 2–5 RepairStrategy objects ranked by patch_score.
- patch_score = correctness×0.5 + maintainability×0.3 − risk×0.2
- Strategies that violate declared architecture boundaries are rejected and
  logged rather than silently dropped.
- All strategy generation is deterministic given identical inputs.
"""
import logging
from dataclasses import dataclass, field
from typing import List, Optional

from backend.core.analysis.schemas import NormalizedFinding
from backend.core.analysis.evidence import EvidenceGraph
from backend.core.analysis.root_cause import RootCauseChain
from backend.core.analysis.localizer import CodeSpan
from backend.core.analysis.repo_graph import RepositoryKnowledgeGraph

logger = logging.getLogger("backend.analysis.patch")


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class RepairStrategy:
    """A single proposed fix strategy."""
    strategy_id:             str
    description:             str
    affected_components:     List[str]     # file paths or symbol names
    target_spans:            List[CodeSpan]
    estimated_risk:          float          # 0.0 (safe) – 1.0 (very risky)
    estimated_correctness:   float          # 0.0 – 1.0
    estimated_maintainability: float        # 0.0 – 1.0
    patch_score:             float          # computed; higher is better
    rollback_description:    str
    rejected:                bool  = False
    rejection_reason:        Optional[str] = None

    @staticmethod
    def compute_score(correctness: float, maintainability: float, risk: float) -> float:
        return round(
            correctness * 0.5 + maintainability * 0.3 - risk * 0.2,
            4,
        )


@dataclass
class PatchPlan:
    """Ordered list of RepairStrategy objects (highest patch_score first)."""
    finding_rule_id: str
    strategies:      List[RepairStrategy] = field(default_factory=list)

    @property
    def best(self) -> Optional[RepairStrategy]:
        active = [s for s in self.strategies if not s.rejected]
        return active[0] if active else None


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class PatchPlanningEngine:
    """
    Produces a PatchPlan for a confirmed NormalizedFinding.

    Strategy generation heuristics
    --------------------------------
    S1 — Direct fix at flagged location (lowest risk, moderate correctness).
    S2 — Fix at root cause site (higher correctness, potentially broader impact).
    S3 — Defensive guard addition upstream of flagged location (lowest risk,
         conservative correctness estimate).

    Additional strategies are produced if the root cause chain contains
    multiple active hypotheses.
    """

    # Architecture boundaries that no strategy may touch
    _PROTECTED_PREFIXES = ("backend/core/", "backend/main.py")

    def plan(
        self,
        finding:    NormalizedFinding,
        root_cause: RootCauseChain,
        graph:      RepositoryKnowledgeGraph,
        spans:      Optional[List[CodeSpan]] = None,
    ) -> PatchPlan:
        plan = PatchPlan(finding_rule_id=finding.rule_id)
        strategies: List[RepairStrategy] = []

        primary_span = (spans[0] if spans else None)
        affected = [finding.file_path] if finding.file_path else []

        # --- S1: direct fix at flagged location --------------------------
        s1 = RepairStrategy(
            strategy_id="S1",
            description=(
                f"Apply a targeted fix at {finding.file_path}:{finding.line_number} "
                f"to address rule '{finding.rule_id}' directly. "
                f"Remediation: {finding.remediation}"
            ),
            affected_components=list(affected),
            target_spans=[primary_span] if primary_span else [],
            estimated_risk=0.20,
            estimated_correctness=finding.static_confidence * 0.85,
            estimated_maintainability=0.75,
            patch_score=0.0,
            rollback_description="Revert the targeted line change via version control.",
        )
        s1.patch_score = RepairStrategy.compute_score(
            s1.estimated_correctness, s1.estimated_maintainability, s1.estimated_risk
        )
        strategies.append(s1)

        # --- S2: fix at root cause site ----------------------------------
        primary_cause = root_cause.primary
        if primary_cause and primary_cause.hypothesis != s1.description:
            rc_affected = list(set(affected + primary_cause.evidence_sources))
            s2 = RepairStrategy(
                strategy_id="S2",
                description=(
                    f"Address root cause: {primary_cause.hypothesis}  "
                    f"Evidence: {', '.join(primary_cause.evidence_sources[:2])}"
                ),
                affected_components=rc_affected,
                target_spans=[primary_span] if primary_span else [],
                estimated_risk=0.40,
                estimated_correctness=primary_cause.confidence * 0.90,
                estimated_maintainability=0.65,
                patch_score=0.0,
                rollback_description=(
                    "Revert root cause site changes and re-run regression suite."
                ),
            )
            s2.patch_score = RepairStrategy.compute_score(
                s2.estimated_correctness, s2.estimated_maintainability, s2.estimated_risk
            )
            strategies.append(s2)

        # --- S3: defensive guard upstream --------------------------------
        callers = []
        if finding.file_path:
            for sym in sorted(graph.symbol_names_in_file(finding.file_path)):
                callers.extend(graph.get_callers(sym))

        s3 = RepairStrategy(
            strategy_id="S3",
            description=(
                f"Add a defensive validation guard in callers "
                f"({', '.join(callers[:2]) or 'none identified'}) "
                f"before reaching the flagged site."
            ),
            affected_components=list(set(affected + callers[:3])),
            target_spans=[],
            estimated_risk=0.15,
            estimated_correctness=0.60,
            estimated_maintainability=0.80,
            patch_score=0.0,
            rollback_description="Remove the added guard conditionals.",
        )
        s3.patch_score = RepairStrategy.compute_score(
            s3.estimated_correctness, s3.estimated_maintainability, s3.estimated_risk
        )
        strategies.append(s3)

        # --- Architecture boundary enforcement ---------------------------
        for s in strategies:
            violated = [
                c for c in s.affected_components
                if any(c.replace("\\", "/").startswith(p) for p in self._PROTECTED_PREFIXES)
            ]
            if violated:
                s.rejected = True
                s.rejection_reason = (
                    f"Strategy touches protected architecture path(s): {violated}"
                )
                logger.warning(
                    "PatchPlanningEngine: strategy '%s' rejected — %s",
                    s.strategy_id, s.rejection_reason,
                )

        # --- Sort active strategies by patch_score desc ------------------
        active   = sorted([s for s in strategies if not s.rejected],
                           key=lambda s: s.patch_score, reverse=True)
        rejected = [s for s in strategies if s.rejected]
        plan.strategies = active + rejected

        logger.info(
            "PatchPlan for rule '%s': %d active, %d rejected strategy(ies).",
            finding.rule_id, len(active), len(rejected),
        )
        return plan
