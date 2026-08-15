"""
Repository Scanner
==================
Walks a directory tree, runs the Phase 3C.2 detection pipeline over every
supported source file, and aggregates the per-file results into a single
``RepositoryReport``.

This is the entry point behind the ``/repository/*`` API routes and the
browser UI. It is deliberately LLM-free: ``DetectionOrchestrator`` runs the
deterministic static path only, so a scan works with no API key and no
network access. Patch *generation* remains opt-in via the existing engines.

Design notes
------------
- Traversal is bounded by ``max_files`` and ``max_file_size_bytes`` so a
  scan of a large monorepo cannot hang the server.
- Vendored, build, and VCS directories are skipped by default.
- A file that fails to parse or analyse is recorded with its error rather
  than aborting the whole scan.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from backend.core.analysis.detection_orchestrator import DetectionOrchestrator
from backend.core.analysis.parsers.registry import ParserRegistry
from backend.core.analysis.repo_graph import RepositoryKnowledgeGraph
from backend.core.ai.memory.semantic import SemanticMemory

logger = logging.getLogger("backend.analysis.repository_scanner")


# Directories that never contain first-party source worth analysing.
DEFAULT_IGNORED_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "venv", ".venv", "env",
    "__pycache__", "build", "out", "dist", "bin", "obj", ".next",
    "third_party", "thirdparty", "vendor", "external", "extern",
    "cmake-build-debug", "cmake-build-release", ".idea", ".vscode",
}

# Extensions the C++ frontend understands.
DEFAULT_EXTENSIONS = ("cpp", "cc", "cxx", "c", "h", "hpp", "hh", "hxx", "inl")

_MAX_FILE_SIZE_BYTES = 1_000_000     # 1 MB
_MAX_FILES           = 2_000


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class StrategyView(BaseModel):
    """A ranked repair strategy from the PatchPlanningEngine."""
    strategy_id:   str
    description:   str
    patch_score:   float
    risk:          float
    correctness:   float
    accepted:      bool = False


class FindingView(BaseModel):
    """A single finding flattened for transport to the UI."""
    finding_id:      str
    rule_id:         str
    severity:        str
    line_number:     Optional[int] = None
    line_text:       str = ""
    description:     str = ""
    evidence:        str = ""
    remediation:     str = ""
    static_confidence: float = 0.0
    confidence:      float = 0.0

    # Phase 3C.2 enrichments
    explanation_markdown: str = ""
    explanation_summary:  str = ""
    root_cause:           str = ""
    root_cause_alternatives: List[str] = Field(default_factory=list)
    eliminated_hypotheses:   int = 0
    evidence_node_count:     int = 0
    strategies:      List[StrategyView] = Field(default_factory=list)
    regression_verdict:  str = "UNKNOWN"
    regression_affected: int = 0


class FileReport(BaseModel):
    """Per-file analysis result."""
    file_path:   str                       # repository-relative, forward slashes
    extension:   str
    line_count:  int = 0
    size_bytes:  int = 0
    findings:    List[FindingView] = Field(default_factory=list)
    suppressed_count: int = 0
    duration_ms: float = 0.0
    error:       Optional[str] = None

    @property
    def finding_count(self) -> int:
        return len(self.findings)


class RepositoryReport(BaseModel):
    """Aggregated result of a full repository scan."""
    scan_id:      str = Field(default_factory=lambda: str(uuid.uuid4()))
    root:         str = ""
    source_label: str = ""                 # what the user pointed us at

    files_scanned:      int = 0
    files_with_findings: int = 0
    files_skipped:      int = 0
    files_errored:      int = 0
    total_findings:     int = 0
    suppressed_count:   int = 0
    truncated:          bool = False       # hit max_files

    severity_counts: Dict[str, int] = Field(default_factory=dict)
    rule_counts:     Dict[str, int] = Field(default_factory=dict)

    files:        List[FileReport] = Field(default_factory=list)
    duration_ms:  float = 0.0
    scanned_at:   float = Field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


class RepositoryScanner:
    """
    Runs the detection pipeline across every supported file in a directory.

    Parameters
    ----------
    extensions          : File extensions to analyse.
    ignored_dirs        : Directory names pruned during traversal.
    max_files           : Upper bound on files analysed in one scan.
    max_file_size_bytes : Files larger than this are skipped.
    """

    def __init__(
        self,
        extensions:          Optional[tuple] = None,
        ignored_dirs:        Optional[set]   = None,
        max_files:           int = _MAX_FILES,
        max_file_size_bytes: int = _MAX_FILE_SIZE_BYTES,
    ) -> None:
        self._extensions = tuple(
            e.lower().lstrip(".") for e in (extensions or DEFAULT_EXTENSIONS)
        )
        self._ignored_dirs = set(ignored_dirs or DEFAULT_IGNORED_DIRS)
        self._max_files = max_files
        self._max_file_size = max_file_size_bytes

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan(self, root: str, source_label: str = "") -> RepositoryReport:
        """
        Analyse every supported source file under *root*.

        Parameters
        ----------
        root         : Absolute path to the repository root.
        source_label : Human-readable origin shown in the UI (e.g. the
                       uploaded archive name). Defaults to *root*.
        """
        t0 = time.perf_counter()
        root = os.path.abspath(root)
        if not os.path.isdir(root):
            raise NotADirectoryError(f"Not a directory: {root}")

        report = RepositoryReport(root=root, source_label=source_label or root)

        # One shared graph and memory across the scan so cross-file reasoning
        # accumulates knowledge as more files are seen.
        shared_graph  = RepositoryKnowledgeGraph()
        shared_memory = SemanticMemory()
        orchestrator  = DetectionOrchestrator(
            semantic_memory=shared_memory,
            repo_graph=shared_graph,
        )

        candidates = self._collect_files(root, report)

        for abs_path, rel_path in candidates:
            file_report = self._analyse_file(orchestrator, abs_path, rel_path)
            report.files.append(file_report)

            if file_report.error:
                report.files_errored += 1
            report.files_scanned += 1
            report.suppressed_count += file_report.suppressed_count

            if file_report.findings:
                report.files_with_findings += 1
                report.total_findings += len(file_report.findings)
                for f in file_report.findings:
                    report.severity_counts[f.severity] = (
                        report.severity_counts.get(f.severity, 0) + 1
                    )
                    report.rule_counts[f.rule_id] = (
                        report.rule_counts.get(f.rule_id, 0) + 1
                    )

        # Most findings first so the UI opens on what matters.
        report.files.sort(key=lambda f: (-len(f.findings), f.file_path))
        report.duration_ms = round((time.perf_counter() - t0) * 1000, 2)

        logger.info(
            "RepositoryScanner: %d file(s), %d finding(s), %d suppressed in %.0fms.",
            report.files_scanned, report.total_findings,
            report.suppressed_count, report.duration_ms,
        )
        return report

    # ------------------------------------------------------------------
    # Traversal
    # ------------------------------------------------------------------

    def _collect_files(self, root: str, report: RepositoryReport) -> List[tuple]:
        """Return [(abs_path, rel_path)] for every analysable file under root."""
        collected: List[tuple] = []

        for dirpath, dirnames, filenames in os.walk(root):
            # Prune ignored and hidden directories in place.
            dirnames[:] = [
                d for d in dirnames
                if d not in self._ignored_dirs and not d.startswith(".")
            ]

            for name in sorted(filenames):
                ext = os.path.splitext(name)[1].lower().lstrip(".")
                if ext not in self._extensions:
                    continue

                abs_path = os.path.join(dirpath, name)
                try:
                    size = os.path.getsize(abs_path)
                except OSError:
                    report.files_skipped += 1
                    continue

                if size > self._max_file_size:
                    report.files_skipped += 1
                    continue

                if len(collected) >= self._max_files:
                    report.truncated = True
                    logger.warning(
                        "RepositoryScanner: max_files (%d) reached — scan truncated.",
                        self._max_files,
                    )
                    return collected

                rel = os.path.relpath(abs_path, root).replace("\\", "/")
                collected.append((abs_path, rel))

        return collected

    # ------------------------------------------------------------------
    # Per-file analysis
    # ------------------------------------------------------------------

    def _analyse_file(
        self,
        orchestrator: DetectionOrchestrator,
        abs_path: str,
        rel_path: str,
    ) -> FileReport:
        ext = os.path.splitext(abs_path)[1].lower().lstrip(".") or "cpp"
        fr = FileReport(file_path=rel_path, extension=ext)

        try:
            fr.size_bytes = os.path.getsize(abs_path)
            with open(abs_path, "r", encoding="utf-8", errors="ignore") as fh:
                code = fh.read()
        except OSError as exc:
            fr.error = f"Could not read file: {exc}"
            return fr

        fr.line_count = code.count("\n") + 1 if code else 0
        if not code.strip():
            return fr

        t0 = time.perf_counter()
        try:
            result = orchestrator.run(
                code=code, extension=ext, file_path=rel_path, render_reports=False
            )
        except Exception as exc:                       # never abort the scan
            logger.error("RepositoryScanner: %s failed — %s", rel_path, exc)
            fr.error = f"{type(exc).__name__}: {exc}"
            fr.duration_ms = round((time.perf_counter() - t0) * 1000, 2)
            return fr

        fr.duration_ms = round((time.perf_counter() - t0) * 1000, 2)
        fr.suppressed_count = result.suppressed_count
        fr.findings = [
            self._to_finding_view(fa, rel_path, idx)
            for idx, fa in enumerate(result.finding_analyses)
        ]
        return fr

    @staticmethod
    def _to_finding_view(fa: Any, rel_path: str, index: int) -> FindingView:
        """Flatten a FindingAnalysis into the transport model."""
        f  = fa.enriched.finding
        cr = fa.enriched.confidence_result
        ex = fa.enriched.explanation

        accepted_ids = {a.strategy.strategy_id for a in fa.remediation.accepted}
        correctness_by_id = {
            a.strategy.strategy_id: a.correctness_estimate
            for a in fa.remediation.accepted
        }

        strategies = [
            StrategyView(
                strategy_id = s.strategy_id,
                description = s.description,
                patch_score = round(s.patch_score, 4),
                risk        = round(s.estimated_risk, 4),
                correctness = round(correctness_by_id.get(s.strategy_id, s.estimated_correctness), 4),
                accepted    = s.strategy_id in accepted_ids,
            )
            for s in fa.patch_plan.strategies
            if not s.rejected
        ]

        primary = fa.root_cause.primary
        alternatives = [
            n.hypothesis for n in fa.root_cause.nodes
            if not n.eliminated and n is not primary
        ][:4]

        return FindingView(
            finding_id  = f"{rel_path}:{f.line_number or 0}:{f.rule_id}:{index}",
            rule_id     = f.rule_id,
            severity    = f.severity,
            line_number = f.line_number,
            line_text   = f.line_text,
            description = f.description,
            evidence    = f.evidence,
            remediation = f.remediation,
            static_confidence = round(f.static_confidence, 4),
            confidence  = round(cr.final_score, 4) if cr else round(f.static_confidence, 4),

            explanation_markdown = ex.markdown if ex else "",
            explanation_summary  = ex.summary  if ex else "",
            root_cause  = primary.hypothesis if primary else "",
            root_cause_alternatives = alternatives,
            eliminated_hypotheses   = fa.root_cause.eliminated_count,
            evidence_node_count     = len(fa.enriched.evidence_graph.nodes)
                                      if fa.enriched.evidence_graph else 0,
            strategies  = strategies,
            regression_verdict  = fa.regression.api_compat_verdict,
            regression_affected = len(fa.regression.affected_components),
        )
