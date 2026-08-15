"""
Confidence Computation Framework
=================================
Produces a reproducible, decomposed confidence score for every
NormalizedFinding.

Score formula
-------------
    confidence = clip(
        w_static   * static_score
      + w_semantic * semantic_score
      + w_consensus* consensus_score
      + w_history  * history_score
      - penalty_contradiction
      - penalty_missing_evidence
      , 0.0, 1.0
    )

All weights are configurable at construction time; defaults reflect
empirically calibrated values for the HBH rule-set.  The formula is
fully deterministic: given identical inputs it MUST produce identical
output.

Every call returns a ConfidenceResult that exposes the full decomposition
so the Explainability Engine can render a per-component breakdown.
"""
import logging
from typing import Dict, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger("backend.analysis.confidence")


# ---------------------------------------------------------------------------
# Data contracts
# ---------------------------------------------------------------------------

class ConfidenceInputs(BaseModel):
    """
    Raw signals consumed by the confidence engine.

    All scores MUST be in [0.0, 1.0].  Optional fields that are absent are
    treated as zero-contribution rather than invalid.
    """
    # Deterministic static-rule strength (BaseRule.confidence × rule weight)
    static_score:    float = Field(ge=0.0, le=1.0)

    # Semantic validation agreement from ValidatorAgent (0 = reject, 1 = confirm)
    semantic_score:  float = Field(default=0.0, ge=0.0, le=1.0)

    # Fraction of participating agents that confirmed the finding (0–1)
    consensus_score: float = Field(default=0.0, ge=0.0, le=1.0)

    # Similarity score against historical confirmed findings in SemanticMemory
    history_score:   float = Field(default=0.0, ge=0.0, le=1.0)

    # Number of agents that explicitly contradicted the finding
    contradiction_count: int = Field(default=0, ge=0)

    # True if evidence graph contains at least one AST/CFG evidence node
    has_evidence_nodes: bool = Field(default=True)


class ConfidenceResult(BaseModel):
    """Fully decomposed confidence output; every field is independently auditable."""
    final_score:           float
    static_contribution:   float
    semantic_contribution: float
    consensus_contribution:float
    history_contribution:  float
    contradiction_penalty: float
    evidence_penalty:      float
    # Raw inputs are embedded for audit reproducibility
    inputs: ConfidenceInputs
    calibrated: bool = False


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class ConfidenceEngine:
    """
    Stateless confidence scoring engine.

    Weights
    -------
    w_static    – weight for deterministic rule evidence   (default 0.45)
    w_semantic  – weight for LLM validation agreement      (default 0.25)
    w_consensus – weight for multi-agent consensus         (default 0.20)
    w_history   – weight for historical similarity         (default 0.10)

    Penalties
    ---------
    contradiction_penalty_per – per-contradicting-agent deduction (default 0.08)
    missing_evidence_penalty  – deduction when no graph evidence node (default 0.10)
    """

    def __init__(
        self,
        w_static:    float = 0.45,
        w_semantic:  float = 0.25,
        w_consensus: float = 0.20,
        w_history:   float = 0.10,
        contradiction_penalty_per: float = 0.08,
        missing_evidence_penalty:  float = 0.10,
    ):
        assert abs(w_static + w_semantic + w_consensus + w_history - 1.0) < 1e-6, \
            "Confidence weights MUST sum to 1.0"
        self.w_static    = w_static
        self.w_semantic  = w_semantic
        self.w_consensus = w_consensus
        self.w_history   = w_history
        self.contradiction_penalty_per = contradiction_penalty_per
        self.missing_evidence_penalty  = missing_evidence_penalty

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compute(self, inputs: ConfidenceInputs) -> ConfidenceResult:
        """Return a fully decomposed ConfidenceResult."""
        static_c    = self.w_static    * inputs.static_score
        semantic_c  = self.w_semantic  * inputs.semantic_score
        consensus_c = self.w_consensus * inputs.consensus_score
        history_c   = self.w_history   * inputs.history_score

        contradiction_p = min(
            inputs.contradiction_count * self.contradiction_penalty_per,
            0.40,   # Cap so a single rogue agent cannot zero-out a finding
        )
        evidence_p = self.missing_evidence_penalty if not inputs.has_evidence_nodes else 0.0

        raw = static_c + semantic_c + consensus_c + history_c \
              - contradiction_p - evidence_p

        final = round(max(0.0, min(1.0, raw)), 4)

        result = ConfidenceResult(
            final_score           = final,
            static_contribution   = round(static_c,    4),
            semantic_contribution = round(semantic_c,  4),
            consensus_contribution= round(consensus_c, 4),
            history_contribution  = round(history_c,   4),
            contradiction_penalty = round(contradiction_p, 4),
            evidence_penalty      = round(evidence_p,  4),
            inputs                = inputs,
        )

        logger.debug(
            "Confidence computed: final=%.4f  "
            "(static=%.4f semantic=%.4f consensus=%.4f history=%.4f "
            "contradiction_penalty=%.4f evidence_penalty=%.4f)",
            final, static_c, semantic_c, consensus_c, history_c,
            contradiction_p, evidence_p,
        )
        return result

    def compute_from_finding(self, static_confidence: float) -> ConfidenceResult:
        """
        Convenience wrapper used when only the static rule score is available
        (i.e. no LLM agents have run yet).  All optional signals default to 0.
        """
        return self.compute(ConfidenceInputs(static_score=static_confidence))
