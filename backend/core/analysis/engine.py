"""
Core Analysis Engine
====================
Coordinates parser selection, rule execution, and the full Phase 3C.2
post-processing pipeline (evidence, confidence, validation, suppression,
explainability) for every analysis request.

Post-processing pipeline (per finding):
    EvidenceGraphBuilder
         ↓
    ConfidenceEngine
         ↓
    ValidationPipeline  (5 stages)
         ↓
    SuppressionPipeline
         ↓
    ExplainabilityEngine
"""
import time
import logging
from typing import Any, Dict, List, Optional

from backend.core.analysis.parsers.registry import ParserRegistry
from backend.core.analysis.rules.registry import RuleRegistry
from backend.core.analysis.rules import rdi_rules  # noqa: F401 — triggers rule registration
from backend.core.analysis.schemas import NormalizedFinding, AnalysisReport

from backend.core.analysis.evidence import EvidenceGraphBuilder, EvidenceGraph
from backend.core.analysis.confidence import ConfidenceEngine, ConfidenceInputs, ConfidenceResult
from backend.core.analysis.validation_pipeline import ValidationPipeline
from backend.core.analysis.suppression import SuppressionPipeline
from backend.core.analysis.explain import ExplainabilityEngine, ExplanationReport
from backend.core.ai.memory.semantic import SemanticMemory

logger = logging.getLogger("backend.analysis")


class EnrichedFinding:
    """
    Thin wrapper around NormalizedFinding that carries Phase 3C.2 enrichments.
    Access the original finding via .finding; additional fields are optional
    so callers that only need NormalizedFinding can ignore them.
    """
    __slots__ = ("finding", "evidence_graph", "confidence_result", "explanation")

    def __init__(
        self,
        finding:          NormalizedFinding,
        evidence_graph:   Optional[EvidenceGraph]    = None,
        confidence_result:Optional[ConfidenceResult] = None,
        explanation:      Optional[ExplanationReport]= None,
    ):
        self.finding           = finding
        self.evidence_graph    = evidence_graph
        self.confidence_result = confidence_result
        self.explanation       = explanation


class AnalysisEngine:
    """
    Core code validation platform coordinating parser selection, rules
    execution, and the full Phase 3C.2 evidence / confidence / validation /
    suppression / explainability pipeline.

    Parameters
    ----------
    semantic_memory : Shared SemanticMemory instance for validation and
                      confidence history signals. If None, a fresh empty
                      instance is used.
    min_confidence  : Minimum confidence threshold forwarded to
                      SuppressionPipeline (default 0.30).
    """

    def __init__(
        self,
        semantic_memory: Optional[SemanticMemory] = None,
        min_confidence:  float = 0.30,
    ):
        self._memory        = semantic_memory or SemanticMemory()
        self._evidence_b    = EvidenceGraphBuilder()
        self._confidence_e  = ConfidenceEngine()
        self._validation_p  = ValidationPipeline(semantic_memory=self._memory)
        self._suppression_p = SuppressionPipeline.default(
            min_confidence=min_confidence,
        )
        self._explain_e     = ExplainabilityEngine()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(
        self,
        code:      str,
        extension: str = "cpp",
        file_path: Optional[str] = None,
    ) -> List[EnrichedFinding]:
        """
        Run the full analysis pipeline against *code*.

        Parameters
        ----------
        code      : Source code to analyse.
        extension : File extension used for parser and rule resolution.
        file_path : Optional path of the analysed file.  When supplied it is
                    stamped onto every finding, which enables downstream
                    localization, cross-file reasoning, regression impact
                    analysis, and SARIF physical locations.  Rules do not set
                    it themselves because they only ever see the code string.

        Returns a list of EnrichedFinding objects for findings that passed
        all validation and suppression stages.  Suppressed findings are
        available via self._suppression_p.audit_log after the call.
        """
        start = time.perf_counter()

        # ── 1. Parse ──────────────────────────────────────────────────
        parser         = ParserRegistry.get_parser(extension)
        representation = parser.parse(code)

        # ── 2. Rule engine ────────────────────────────────────────────
        rules = RuleRegistry.get_rules_for_language(
            representation.metadata["language"]
        )
        raw_findings: List[NormalizedFinding] = []
        for rule in rules:
            try:
                t0 = time.perf_counter()
                raw_findings.extend(rule.execute(representation))
                logger.debug("Rule %s: %.4fs", rule.rule_id, time.perf_counter() - t0)
            except Exception as exc:
                logger.error("Rule execution failure [%s]: %s", rule.rule_id, exc)

        # Stamp the source location onto every finding. Rules only ever see a
        # code string, so file_path can only be attached here.
        if file_path:
            for f in raw_findings:
                f.file_path = file_path

        # ── 3. Per-finding Phase 3C.2 pipeline ────────────────────────
        validated_findings: List[NormalizedFinding] = []
        # Keyed by object identity, NOT rule_id: a single rule can fire many
        # times in one file and each occurrence has its own evidence graph,
        # confidence result, and explanation. `validated_findings` keeps every
        # key object alive for the lifetime of this call.
        enrichments:        Dict[int, Any]           = {}

        for f in raw_findings:
            # 3a. Evidence graph
            eg = self._evidence_b.build(f)

            # 3b. Confidence
            ci = ConfidenceInputs(static_score=f.static_confidence)
            cr = self._confidence_e.compute(ci)

            # 3c. Validation pipeline (5 stages)
            vr = self._validation_p.run(f, eg, cr)
            if not vr.passed:
                logger.debug("Finding rejected at validation: %s", f.rule_id)
                continue                         # hard reject — skip entirely

            # Use (possibly downgraded) finding from validation
            f_out = vr.finding

            # 3d. Explanation (before suppression so we have it if needed)
            exp = self._explain_e.explain(
                finding=f_out,
                graph=eg,
                confidence=cr,
            )

            enrichments[id(f_out)] = {
                "evidence_graph":    eg,
                "confidence_result": cr,
                "explanation":       exp,
            }
            validated_findings.append(f_out)

        # ── 4. Suppression pipeline (operates on list) ─────────────────
        supp_result = self._suppression_p.run(validated_findings)
        passed = supp_result.passed

        # ── 5. Build EnrichedFinding list ─────────────────────────────
        enriched: List[EnrichedFinding] = []
        for f in passed:
            enr = enrichments.get(id(f), {})
            enriched.append(EnrichedFinding(
                finding=f,
                evidence_graph=enr.get("evidence_graph"),
                confidence_result=enr.get("confidence_result"),
                explanation=enr.get("explanation"),
            ))

        duration = time.perf_counter() - start
        logger.info(
            "Analysis complete: %d lines, %d rules, %d raw → %d validated → %d passed in %.4fs",
            representation.metadata["total_lines"],
            len(rules),
            len(raw_findings),
            len(validated_findings),
            len(passed),
            duration,
        )
        return enriched

    def analyze_plain(
        self,
        code:      str,
        extension: str = "cpp",
        file_path: Optional[str] = None,
    ) -> List[NormalizedFinding]:
        """
        Backward-compatible wrapper: returns plain NormalizedFinding list.
        Used by existing API routes and MCP tools that pre-date Phase 3C.2.
        """
        return [ef.finding for ef in self.analyze(code, extension, file_path)]

