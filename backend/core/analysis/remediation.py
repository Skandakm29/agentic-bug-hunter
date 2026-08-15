"""
Remediation Reasoner  (§122)
==============================
Evaluates RepairStrategy objects from a PatchPlan against architecture rules,
repository conventions, historical fix patterns (SemanticMemory), and
organization policies. Produces a RemediationPlan containing accepted
strategies with rollback descriptions and a rejection log for declined ones.

Design invariants
-----------------
- Strategies are evaluated in patch_score order (highest first).
- At least one strategy MUST survive unless all violate hard constraints.
- If all strategies are rejected, a NO_VIABLE_STRATEGY record is produced.
- Correctness estimation: static_scope × 0.4 + semantic_similarity × 0.4 + agent_consensus × 0.2
- All decisions are logged for audit.
"""
import logging
from dataclasses import dataclass, field
from typing import List, Optional

from backend.core.analysis.patch import PatchPlan, RepairStrategy
from backend.core.ai.memory.semantic import SemanticMemory

logger = logging.getLogger("backend.analysis.remediation")

_NO_VIABLE_STRATEGY = "NO_VIABLE_STRATEGY"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class AcceptedStrategy:
    """A repair strategy approved by the reasoner."""
    strategy:              RepairStrategy
    correctness_estimate:  float
    rollback_description:  str
    semantic_similarity:   float = 0.0


@dataclass
class RejectionRecord:
    """A rejected strategy with structured reason."""
    strategy_id: str
    reason:      str


@dataclass
class RemediationPlan:
    """Accepted strategies + rejection log produced by RemediationReasoner."""
    finding_rule_id:  str
    accepted:         List[AcceptedStrategy] = field(default_factory=list)
    rejected:         List[RejectionRecord]  = field(default_factory=list)
    no_viable:        bool = False

    @property
    def best(self) -> Optional[AcceptedStrategy]:
        return self.accepted[0] if self.accepted else None


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class RemediationReasoner:
    """
    Evaluates a PatchPlan and produces a RemediationPlan.

    Parameters
    ----------
    semantic_memory        : Shared SemanticMemory for historical similarity.
    min_correctness        : Minimum correctness estimate to accept (default 0.40).
    agent_consensus_score  : Consensus fraction from multi-agent runtime (default 0.0
                             when no agents have run).
    """

    def __init__(
        self,
        semantic_memory:       Optional[SemanticMemory] = None,
        min_correctness:       float = 0.40,
        agent_consensus_score: Optional[float] = None,
    ):
        self.memory               = semantic_memory or SemanticMemory()
        self.min_correctness      = min_correctness
        # None means "no agents have run", which is distinct from a genuine
        # consensus of 0.0 (all agents contradicted the strategy).
        self.agent_consensus_score= agent_consensus_score

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate(self, plan: PatchPlan) -> RemediationPlan:
        """
        Evaluate all non-rejected strategies in *plan* and produce a
        RemediationPlan.
        """
        result = RemediationPlan(finding_rule_id=plan.finding_rule_id)

        candidates = [s for s in plan.strategies if not s.rejected]

        for strategy in candidates:
            verdict, reason, correctness, sim = self._evaluate_strategy(strategy)
            if verdict:
                result.accepted.append(AcceptedStrategy(
                    strategy=strategy,
                    correctness_estimate=correctness,
                    rollback_description=strategy.rollback_description,
                    semantic_similarity=sim,
                ))
            else:
                result.rejected.append(RejectionRecord(
                    strategy_id=strategy.strategy_id,
                    reason=reason or "unknown",
                ))
                logger.warning(
                    "RemediationReasoner: strategy '%s' rejected — %s",
                    strategy.strategy_id, reason,
                )

        # Carry over strategies already rejected by PatchPlanningEngine
        for s in plan.strategies:
            if s.rejected:
                result.rejected.append(RejectionRecord(
                    strategy_id=s.strategy_id,
                    reason=s.rejection_reason or "rejected by PatchPlanningEngine",
                ))

        if not result.accepted:
            result.no_viable = True
            logger.error(
                "RemediationReasoner: no viable strategy for rule '%s'. "
                "Producing NO_VIABLE_STRATEGY record.",
                plan.finding_rule_id,
            )

        logger.info(
            "RemediationPlan for rule '%s': %d accepted, %d rejected.",
            plan.finding_rule_id, len(result.accepted), len(result.rejected),
        )
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _evaluate_strategy(
        self, s: RepairStrategy
    ):
        """
        Returns (accepted: bool, reason: str, correctness: float, semantic_sim: float).
        """
        # 1. Semantic memory similarity
        sim_results = self.memory.search(s.description, top_k=1)
        sim = sim_results[0]["score"] if sim_results else 0.0

        # 2. Correctness estimate.
        #
        #    The nominal formula is
        #        static x 0.40 + semantic x 0.40 + consensus x 0.20
        #    but semantic similarity and agent consensus are *optional*
        #    signals. Scoring an unavailable signal as 0.0 does not express
        #    "no evidence", it actively penalises the strategy: with an empty
        #    SemanticMemory and no agents the score is capped at 0.40 x static,
        #    which can never clear a 0.40 floor, so every strategy was rejected
        #    and every plan came back NO_VIABLE_STRATEGY.
        #
        #    Instead, weight only the signals that are actually present and
        #    renormalise so the weights always sum to 1.0.
        weighted: List[tuple] = [(0.40, s.estimated_correctness)]
        if self.memory.kb:
            weighted.append((0.40, sim))
        if self.agent_consensus_score is not None:
            weighted.append((0.20, self.agent_consensus_score))

        total_weight = sum(w for w, _ in weighted)
        correctness  = round(
            sum(w * v for w, v in weighted) / total_weight,
            4,
        )

        # 3. Hard rejection: correctness too low
        if correctness < self.min_correctness:
            return (
                False,
                f"correctness estimate {correctness:.4f} < minimum {self.min_correctness:.4f}",
                correctness, sim,
            )

        # 4. Hard rejection: risk too high (> 0.85)
        if s.estimated_risk > 0.85:
            return (
                False,
                f"estimated risk {s.estimated_risk:.2f} exceeds maximum 0.85",
                correctness, sim,
            )

        return True, "", correctness, sim
