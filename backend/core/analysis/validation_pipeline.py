"""
Multi-Stage Validation Pipeline  (§124)
=========================================
Every finding MUST pass through five sequential validation stages before it
can be surfaced to developers or downstream consumers.

Stage 1 — Evidence Validation
    EvidenceGraph MUST contain ≥ 1 non-FINDING, non-DOCUMENTATION node.

Stage 2 — Semantic Validation
    Finding must not be flagged as a false positive by the SemanticMemory
    historical similarity check (threshold configurable, default 0.80 similarity
    to a known-false-positive pattern triggers rejection).

Stage 3 — Architecture Validation
    Finding file_path must not be in a whitelisted architecture layer
    (e.g. generated code directories).

Stage 4 — Consistency Validation
    Finding is consistent with at least one confirmed pattern in SemanticMemory
    OR has static_confidence ≥ 0.70.

Stage 5 — Confidence Threshold
    final_score from ConfidenceEngine ≥ configured threshold (default 0.30).

Failure semantics
-----------------
- Stage 1 failure: verdict = REJECTED / reason = INSUFFICIENT_EVIDENCE
- Stages 2–4 failure: finding is DOWNGRADED (severity → LOW, semantic_validated=False)
- Stage 5 failure: finding is passed to SuppressionPipeline (handled externally)

Design invariants
-----------------
- Every stage decision is logged with stage name, verdict, and reason.
- Given identical inputs, replay MUST produce identical verdicts.
"""
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from backend.core.analysis.evidence import EvidenceGraph, EvidenceNodeType
from backend.core.analysis.confidence import ConfidenceResult
from backend.core.analysis.schemas import NormalizedFinding
from backend.core.ai.memory.semantic import SemanticMemory

logger = logging.getLogger("backend.analysis.validation_pipeline")


# ---------------------------------------------------------------------------
# Verdicts & stage results
# ---------------------------------------------------------------------------

class StageVerdict(str, Enum):
    PASSED   = "PASSED"
    DEGRADED = "DEGRADED"
    REJECTED = "REJECTED"


@dataclass
class StageResult:
    stage_name: str
    verdict:    StageVerdict
    reason:     str = ""


@dataclass
class ValidationResult:
    """Complete result of running a finding through the pipeline."""
    finding:          NormalizedFinding      # may be mutated (severity downgraded)
    passed:           bool
    semantic_validated: bool = True
    stages:           List[StageResult] = field(default_factory=list)
    final_verdict:    StageVerdict = StageVerdict.PASSED

    def add_stage(self, result: StageResult) -> None:
        self.stages.append(result)
        logger.debug(
            "ValidationPipeline [%s] → %s%s",
            result.stage_name, result.verdict.value,
            f": {result.reason}" if result.reason else "",
        )


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class ValidationPipeline:
    """
    Five-stage validation pipeline for NormalizedFinding objects.

    Parameters
    ----------
    semantic_memory            : Shared SemanticMemory instance.
    confidence_threshold       : Minimum final_score to pass Stage 5 (default 0.30).
    false_positive_sim_threshold: SemanticMemory similarity threshold above which
                                  a known-false-positive match triggers Stage 2
                                  degradation (default 0.80).
    architecture_whitelist     : Directory prefixes exempt from findings (Stage 3).
    consistency_confidence_floor: Static confidence floor that bypasses Stage 4
                                  similarity check (default 0.70).
    """

    _EVIDENCE_NODE_TYPES = (
        EvidenceNodeType.AST_NODE,
        EvidenceNodeType.CFG_BLOCK,
        EvidenceNodeType.DFG_VARIABLE,
        EvidenceNodeType.SYMBOL,
        EvidenceNodeType.FUNCTION,
        EvidenceNodeType.CLASS,
        EvidenceNodeType.API_ENDPOINT,
        EvidenceNodeType.FILE,
        EvidenceNodeType.COMMIT,
        EvidenceNodeType.RETRIEVED_CTX,
    )

    def __init__(
        self,
        semantic_memory:              Optional[SemanticMemory] = None,
        confidence_threshold:         float = 0.30,
        false_positive_sim_threshold: float = 0.80,
        architecture_whitelist:       Optional[List[str]] = None,
        consistency_confidence_floor: float = 0.70,
    ):
        self.memory                       = semantic_memory or SemanticMemory()
        self.confidence_threshold         = confidence_threshold
        self.fp_sim_threshold             = false_positive_sim_threshold
        self.arch_whitelist               = [
            p.lower() for p in (architecture_whitelist or [
                "generated/", "vendor/", "third_party/", ".git/",
            ])
        ]
        self.consistency_floor            = consistency_confidence_floor

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        finding:    NormalizedFinding,
        graph:      EvidenceGraph,
        confidence: ConfidenceResult,
    ) -> ValidationResult:
        """
        Execute all five stages against *finding* and return a ValidationResult.
        """
        result = ValidationResult(finding=finding, passed=True)

        # Stage 1 — Evidence Validation
        s1 = self._stage_evidence(finding, graph)
        result.add_stage(s1)
        if s1.verdict == StageVerdict.REJECTED:
            result.passed = False
            result.final_verdict = StageVerdict.REJECTED
            return result  # early exit — no further stages make sense

        # Stage 2 — Semantic Validation
        s2 = self._stage_semantic(finding)
        result.add_stage(s2)
        if s2.verdict == StageVerdict.DEGRADED:
            result.semantic_validated = False
            result.finding = self._downgrade_severity(finding)

        # Stage 3 — Architecture Validation
        s3 = self._stage_architecture(finding)
        result.add_stage(s3)
        if s3.verdict == StageVerdict.DEGRADED:
            result.semantic_validated = False
            result.finding = self._downgrade_severity(finding)

        # Stage 4 — Consistency Validation
        s4 = self._stage_consistency(finding)
        result.add_stage(s4)
        if s4.verdict == StageVerdict.DEGRADED:
            result.finding = self._downgrade_severity(finding)

        # Stage 5 — Confidence Threshold
        s5 = self._stage_confidence(confidence)
        result.add_stage(s5)
        if s5.verdict == StageVerdict.DEGRADED:
            # Passed to SuppressionPipeline which will apply ConfidenceThresholdFilter
            result.final_verdict = StageVerdict.DEGRADED
        else:
            result.final_verdict = StageVerdict.PASSED

        logger.info(
            "ValidationPipeline: rule '%s' → %s (semantic_validated=%s).",
            finding.rule_id, result.final_verdict.value, result.semantic_validated,
        )
        return result

    # ------------------------------------------------------------------
    # Stage implementations
    # ------------------------------------------------------------------

    def _stage_evidence(self, finding: NormalizedFinding, graph: EvidenceGraph) -> StageResult:
        non_meta_nodes = [
            n for n in graph.nodes.values()
            if n.node_type in self._EVIDENCE_NODE_TYPES
        ]
        if non_meta_nodes:
            return StageResult("Stage1_Evidence", StageVerdict.PASSED)
        return StageResult(
            "Stage1_Evidence", StageVerdict.REJECTED,
            reason="INSUFFICIENT_EVIDENCE — EvidenceGraph contains no code entity nodes.",
        )

    def _stage_semantic(self, finding: NormalizedFinding) -> StageResult:
        # Search for known-false-positive patterns in SemanticMemory
        results = self.memory.search(
            f"false positive {finding.rule_id} {finding.description}", top_k=1
        )
        if results and results[0]["score"] >= self.fp_sim_threshold:
            return StageResult(
                "Stage2_Semantic", StageVerdict.DEGRADED,
                reason=(
                    f"Matches known false-positive pattern with similarity "
                    f"{results[0]['score']:.4f} ≥ {self.fp_sim_threshold:.4f}."
                ),
            )
        return StageResult("Stage2_Semantic", StageVerdict.PASSED)

    def _stage_architecture(self, finding: NormalizedFinding) -> StageResult:
        fp = (finding.file_path or "").replace("\\", "/").lower()
        hit = next((p for p in self.arch_whitelist if fp.startswith(p)), None)
        if hit:
            return StageResult(
                "Stage3_Architecture", StageVerdict.DEGRADED,
                reason=f"file_path '{finding.file_path}' is under whitelisted layer '{hit}'.",
            )
        return StageResult("Stage3_Architecture", StageVerdict.PASSED)

    def _stage_consistency(self, finding: NormalizedFinding) -> StageResult:
        if finding.static_confidence >= self.consistency_floor:
            return StageResult(
                "Stage4_Consistency", StageVerdict.PASSED,
                reason=f"static_confidence {finding.static_confidence:.4f} ≥ floor {self.consistency_floor:.4f}.",
            )
        results = self.memory.search(
            f"{finding.rule_id} {finding.description} confirmed", top_k=1
        )
        if results and results[0]["score"] > 0.30:
            return StageResult(
                "Stage4_Consistency", StageVerdict.PASSED,
                reason=f"SemanticMemory consistency match score {results[0]['score']:.4f}.",
            )
        return StageResult(
            "Stage4_Consistency", StageVerdict.DEGRADED,
            reason=(
                f"static_confidence {finding.static_confidence:.4f} < floor "
                f"and no consistent SemanticMemory match found."
            ),
        )

    def _stage_confidence(self, confidence: ConfidenceResult) -> StageResult:
        if confidence.final_score >= self.confidence_threshold:
            return StageResult(
                "Stage5_Confidence", StageVerdict.PASSED,
                reason=f"final_score {confidence.final_score:.4f} ≥ {self.confidence_threshold:.4f}.",
            )
        return StageResult(
            "Stage5_Confidence", StageVerdict.DEGRADED,
            reason=(
                f"final_score {confidence.final_score:.4f} < threshold "
                f"{self.confidence_threshold:.4f} — will be suppressed."
            ),
        )

    @staticmethod
    def _downgrade_severity(finding: NormalizedFinding) -> NormalizedFinding:
        """Return a copy of *finding* with severity downgraded to LOW."""
        data = finding.model_dump()
        if data["severity"] != "LOW":
            data["severity"] = "LOW"
        return NormalizedFinding(**data)
