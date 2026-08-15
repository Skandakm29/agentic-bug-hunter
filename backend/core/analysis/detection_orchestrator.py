"""
Detection Orchestrator  (§109 + Phase 3D.1)
=============================================
End-to-end pipeline entry point combining all Phase 3C.2 and 3D.1 subsystems.
This façade is the single interface callers use for full analysis; it
coordinates AnalysisEngine, RootCauseAnalyzer, BugLocalizer, CrossFileReasoner,
PatchPlanningEngine, RemediationReasoner, RegressionAnalyzer, ReportGenerator,
and (optionally) PatchGenerationEngine.

Usage
-----
    from backend.core.analysis.detection_orchestrator import DetectionOrchestrator, OrchestratorResult

    orch   = DetectionOrchestrator()
    result = orch.run(code="...", extension="cpp")
    # result.enriched_findings  — List[EnrichedFinding]
    # result.json_report        — bytes
    # result.sarif_report       — bytes
    # result.markdown_report    — bytes

Execution order
---------------
    parse + rule engine          (AnalysisEngine)
         ↓
    evidence + confidence        (inside AnalysisEngine)
         ↓
    validation + suppression     (inside AnalysisEngine)
         ↓
    localization                 (BugLocalizer per finding)
         ↓
    cross-file tracing           (CrossFileReasoner per file)
         ↓
    root cause analysis          (RootCauseAnalyzer per finding)
         ↓
    patch planning               (PatchPlanningEngine per finding)
         ↓
    remediation reasoning        (RemediationReasoner per plan)
         ↓
    regression impact            (RegressionAnalyzer per remediation plan)
         ↓
    patch generation [OPTIONAL]  (PatchGenerationEngine per finding — Phase 3D.1)
         ↓
    report generation            (ReportGenerator — JSON, SARIF, Markdown)
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import re

from backend.core.analysis.engine import AnalysisEngine, EnrichedFinding
from backend.core.analysis.schemas import NormalizedFinding, AnalysisReport
from backend.core.analysis.repo_graph import RepositoryKnowledgeGraph, SymbolNode
from backend.core.analysis.dfg import DFGConstructor, DataDependency
from backend.core.analysis.ipa import IPATracer, InterproceduralCallGraph
from backend.core.analysis.localizer import BugLocalizer, CodeSpan
from backend.core.analysis.cross_file import CrossFileReasoner
from backend.core.analysis.root_cause import RootCauseAnalyzer, RootCauseChain
from backend.core.analysis.patch import PatchPlanningEngine, PatchPlan
from backend.core.analysis.remediation import RemediationReasoner, RemediationPlan
from backend.core.analysis.regression import RegressionAnalyzer, RegressionImpactReport
from backend.core.analysis.report import ReportGenerator, ReportFormat, EnrichedReport
from backend.core.ai.memory.semantic import SemanticMemory

# Phase 3D.1 — optional patch generation integration
try:
    from backend.core.patch_generation.patch_generator import PatchGenerationEngine
    from backend.core.patch_generation.patch_models import StructuredPatch
    _PATCH_GENERATION_AVAILABLE = True
except ImportError:  # pragma: no cover
    PatchGenerationEngine = None  # type: ignore[assignment,misc]
    StructuredPatch       = None  # type: ignore[assignment,misc]
    _PATCH_GENERATION_AVAILABLE = False

logger = logging.getLogger("backend.analysis.detection_orchestrator")

# Wall-clock cap for one off-thread patch generation call.
_PATCH_GENERATION_TIMEOUT_S = 60


# ---------------------------------------------------------------------------
# Per-finding full analysis record
# ---------------------------------------------------------------------------

@dataclass
class FindingAnalysis:
    """All Phase 3C.2 + 3D.1 artefacts for a single finding."""
    enriched:       EnrichedFinding
    spans:          List[CodeSpan]
    root_cause:     RootCauseChain
    patch_plan:     PatchPlan
    remediation:    RemediationPlan
    regression:     RegressionImpactReport
    # Cross-module trace seeded from the finding's file / enclosing symbol
    cross_file:     Optional[Any] = None    # CrossFileTrace | None
    # Phase 3D.1 — populated when a PatchGenerationEngine is configured
    structured_patch: Optional[Any] = None  # StructuredPatch | None


# ---------------------------------------------------------------------------
# Orchestrator result
# ---------------------------------------------------------------------------

@dataclass
class OrchestratorResult:
    """Complete output of DetectionOrchestrator.run()."""
    enriched_findings:  List[EnrichedFinding]
    finding_analyses:   List[FindingAnalysis]
    json_report:        bytes
    sarif_report:       bytes
    markdown_report:    bytes
    html_report:        bytes
    duration_seconds:   float
    suppressed_count:   int  = 0
    # Phase 3D.1 — patch_id → StructuredPatch mapping for each finding
    structured_patches: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class DetectionOrchestrator:
    """
    Full Phase 3C.2 detection pipeline façade.

    Parameters
    ----------
    semantic_memory     : Shared SemanticMemory (propagated to all subsystems).
    repo_graph          : Pre-populated RepositoryKnowledgeGraph, or None for a
                          fresh empty graph (sufficient for single-file analysis).
    min_confidence      : Minimum confidence threshold (default 0.30).
    max_traversal_depth : CrossFile / MultiHop traversal depth cap (default 5).
    """

    def __init__(
        self,
        semantic_memory:      Optional[SemanticMemory]           = None,
        repo_graph:           Optional[RepositoryKnowledgeGraph] = None,
        min_confidence:       float = 0.30,
        max_traversal_depth:  int   = 5,
        patch_engine:         Optional[Any] = None,  # PatchGenerationEngine | None
    ):
        mem = semantic_memory or SemanticMemory()
        self._graph        = repo_graph or RepositoryKnowledgeGraph()
        self._engine       = AnalysisEngine(semantic_memory=mem, min_confidence=min_confidence)
        self._localizer    = BugLocalizer()
        self._cross_file   = CrossFileReasoner(max_depth=max_traversal_depth)
        self._root_cause   = RootCauseAnalyzer()
        self._patcher      = PatchPlanningEngine()
        self._remediator   = RemediationReasoner(semantic_memory=mem)
        self._regression   = RegressionAnalyzer()
        self._reporter     = ReportGenerator()
        # Phase 3D.1 — optional; if not provided, patch generation is skipped
        self._patch_engine: Optional[Any] = patch_engine

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        code:      str,
        extension: str = "cpp",
        file_path: Optional[str] = None,
        render_reports: bool = True,
    ) -> OrchestratorResult:
        """
        Execute the full Phase 3C.2 + 3D.1 pipeline and return an OrchestratorResult.

        Parameters
        ----------
        code      : Source code string to analyse.
        extension : File extension used for parser and rule resolution.
        file_path : Optional file path used for cross-file reasoning seed.
        """
        t0 = time.perf_counter()
        logger.info("DetectionOrchestrator.run() started (extension=%s).", extension)

        # ── 1. Static + Phase 3C.2 core pipeline ──────────────────────
        enriched_findings = self._engine.analyze(code, extension, file_path)
        suppressed_count  = len(self._engine._suppression_p.audit_log)

        # ── 1b. Program graphs (Phase 3A) ─────────────────────────────
        # Built once per run and shared by every finding. Without these the
        # RootCauseAnalyzer can only ever emit its trivial H1 hypothesis.
        dfg_deps: List[DataDependency] = DFGConstructor().construct(
            code.splitlines()
        ).dependencies
        call_graph: InterproceduralCallGraph = IPATracer().trace_calls(code)
        self._seed_repo_graph(code, call_graph, file_path)

        # ── 2. Per-finding deep analysis ───────────────────────────────
        analyses: List[FindingAnalysis] = []

        for ef in enriched_findings:
            fa = self._analyse_finding(
                ef,
                source_code=code,
                dfg_deps=dfg_deps,
                call_graph=call_graph,
            )
            analyses.append(fa)

        # ── 3. Build plain NormalizedFinding list for AnalysisReport ──
        plain_findings = [ef.finding for ef in enriched_findings]
        analysis_report = AnalysisReport(
            static_findings=plain_findings,
            total_issues=len(plain_findings),
            llm_available=False,   # synchronous path; LLM agents run separately
        )

        # ── 4. Build explanation markdown map ─────────────────────────
        explanation_md: Dict[str, str] = {}
        for ef in enriched_findings:
            if ef.explanation:
                explanation_md[ef.finding.rule_id] = ef.explanation.markdown

        # ── 5. Collect structured patches (Phase 3D.1) ─────────────────
        structured_patches: Dict[str, Any] = {
            fa.enriched.finding.rule_id: fa.structured_patch
            for fa in analyses
            if fa.structured_patch is not None
        }

        # ── 6. Generate reports ────────────────────────────────────────
        enriched_report = EnrichedReport(
            report=analysis_report,
            explanation_markdowns=explanation_md,
            root_cause_summaries={
                fa.enriched.finding.rule_id: (
                    fa.root_cause.primary.hypothesis
                    if fa.root_cause.primary else "N/A"
                )
                for fa in analyses
            },
            regression_summaries={
                fa.enriched.finding.rule_id: (
                    f"api={fa.regression.api_compat_verdict}, "
                    f"affected={len(fa.regression.affected_components)}"
                )
                for fa in analyses
            },
            structured_patches=structured_patches,
        )

        # A repository scan aggregates its own report across all files and
        # discards these, so rendering four formats per file is pure waste.
        if render_reports:
            json_bytes     = self._reporter.generate(enriched_report, ReportFormat.JSON)
            sarif_bytes    = self._reporter.generate(enriched_report, ReportFormat.SARIF)
            markdown_bytes = self._reporter.generate(enriched_report, ReportFormat.MARKDOWN)
            html_bytes     = self._reporter.generate(enriched_report, ReportFormat.HTML)
        else:
            json_bytes = sarif_bytes = markdown_bytes = html_bytes = b""

        duration = time.perf_counter() - t0
        logger.info(
            "DetectionOrchestrator complete: %d findings, %d suppressed, "
            "%d patch(es) generated, %.3fs.",
            len(enriched_findings), suppressed_count,
            len(structured_patches), duration,
        )

        return OrchestratorResult(
            enriched_findings=enriched_findings,
            finding_analyses=analyses,
            json_report=json_bytes,
            sarif_report=sarif_bytes,
            markdown_report=markdown_bytes,
            html_report=html_bytes,
            duration_seconds=round(duration, 4),
            suppressed_count=suppressed_count,
            structured_patches=structured_patches,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _analyse_finding(
        self,
        ef:          EnrichedFinding,
        source_code: str = "",
        dfg_deps:    Optional[List[DataDependency]] = None,
        call_graph:  Optional[InterproceduralCallGraph] = None,
    ) -> FindingAnalysis:
        """
        Run per-finding deep analysis:
        localize → cross-file trace → root cause → patch plan → remediate →
        regression → [optional] patch generation (Phase 3D.1).
        """
        f = ef.finding

        # Localization
        spans = self._localizer.locate(f, self._graph)

        # Cross-file trace, seeded from the enclosing symbol when the localizer
        # resolved one, otherwise from the file itself.
        cross_trace = None
        seed = next((s.symbol_name for s in spans if s.symbol_name), None) or f.file_path
        if seed:
            try:
                cross_trace = self._cross_file.trace(self._graph, seed)
            except Exception as exc:
                logger.debug("CrossFileReasoner trace failed for '%s': %s", seed, exc)

        # Root cause — DFG slice (H2) and IPA caller traversal (H3) only fire
        # when the program graphs are supplied.
        rc = self._root_cause.analyze(
            f, self._graph, dfg_deps=dfg_deps, call_graph=call_graph
        )

        # Patch plan
        pp = self._patcher.plan(f, rc, self._graph, spans)

        # Remediation
        rp = self._remediator.evaluate(pp)

        # Regression
        reg = self._regression.analyze(rp, self._graph)

        logger.debug(
            "FindingAnalysis for '%s': spans=%d rc_primary='%s' patch_strategies=%d reg_affected=%d",
            f.rule_id, len(spans),
            rc.primary.hypothesis[:60] if rc.primary else "none",
            len(pp.strategies),
            len(reg.affected_components),
        )

        # ── Phase 3D.1: autonomous patch generation (optional) ────────
        structured_patch: Optional[Any] = None
        if self._patch_engine is not None and source_code:
            # generate() is async but run() is a synchronous façade, so the
            # coroutine has to be driven differently depending on whether a
            # loop is already running in this thread (e.g. under FastAPI).
            # The coroutine is built lazily by this factory so exactly one is
            # ever created -- constructing it up-front on a branch that is not
            # taken leaves an un-awaited coroutine behind.
            def _new_coro():
                return self._patch_engine.generate(
                    finding    = f,
                    code       = source_code,
                    root_cause = rc,
                    evidence   = getattr(ef, "evidence_graph", None),
                    bug_id     = f.rule_id,
                )

            try:
                try:
                    asyncio.get_running_loop()
                except RuntimeError:
                    # No loop running in this thread — drive it directly.
                    structured_patch = asyncio.run(_new_coro())
                else:
                    # A loop owns this thread; asyncio.run() would raise.
                    # Hand the work to a worker thread with its own loop.
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                        structured_patch = ex.submit(
                            lambda: asyncio.run(_new_coro())
                        ).result(timeout=_PATCH_GENERATION_TIMEOUT_S)
            except Exception as patch_err:
                logger.warning(
                    "DetectionOrchestrator: patch generation failed for '%s': %s — "
                    "continuing without patch.",
                    f.rule_id, patch_err,
                )

        return FindingAnalysis(
            enriched=ef, spans=spans, root_cause=rc,
            patch_plan=pp, remediation=rp, regression=reg,
            cross_file=cross_trace,
            structured_patch=structured_patch,
        )

    # ------------------------------------------------------------------
    # Repository graph seeding
    # ------------------------------------------------------------------

    # Matches a C/C++ function definition opening brace, e.g. "void foo(int a) {"
    _FUNC_DECL_RE = re.compile(r'^\s*[\w:*&<>\s]+?\b(\w+)\s*\([^;]*\)\s*(?:const\s*)?\{')

    def _seed_repo_graph(
        self,
        code:       str,
        call_graph: InterproceduralCallGraph,
        file_path:  Optional[str],
    ) -> None:
        """
        Populate the RepositoryKnowledgeGraph from the analysed file.

        Single-file callers pass no pre-built graph, which previously left the
        localizer, root-cause H3, patch strategy S3, and regression analysis
        with nothing to traverse. Registering the functions and call edges we
        already parsed makes those subsystems operate on real data.

        Idempotent: symbols are keyed by name and call edges are stored in
        sets, so re-seeding across runs does not duplicate anything.
        """
        resolved_path = file_path or "<in-memory>"

        for i, line in enumerate(code.splitlines(), start=1):
            m = self._FUNC_DECL_RE.match(line)
            if m:
                self._graph.register_symbol(SymbolNode(
                    name=m.group(1),
                    symbol_type="function",
                    file_path=resolved_path,
                    line_number=i,
                ))

        for edge in call_graph.edges:
            if edge.caller != "global":
                self._graph.add_call(edge.caller, edge.callee)

        logger.debug(
            "DetectionOrchestrator: repo graph seeded — %d symbol(s), %d caller(s).",
            len(self._graph.symbols), len(self._graph.call_graph),
        )
