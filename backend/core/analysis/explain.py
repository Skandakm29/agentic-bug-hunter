"""
Explainability Engine
=====================
Generates structured, developer-facing explanations for every
NormalizedFinding by traversing its EvidenceGraph and embedding the
ConfidenceResult decomposition.

Output contract
---------------
Every explanation SHALL answer:
  • Why was this detected?      → rule metadata + triggered conditions
  • Which evidence supports it? → evidence graph node listing
  • Which rules fired?          → rule_id + category + severity
  • Which agents participated?  → populated by orchestrator metadata
  • Which execution paths?      → CFG/DFG nodes in the graph
  • Which assumptions?          → low-confidence or unresolved nodes
  • Competing hypotheses?       → suppressed / alternative findings

The primary render target is Markdown (human-readable in PRs, IDEs, and
issue trackers).  A compact ``summary`` field is also produced for
token-constrained LLM contexts.
"""
import logging
from typing import List, Optional

from backend.core.analysis.evidence import (
    EvidenceGraph,
    EvidenceNode,
    EvidenceNodeType,
    EvidenceEdgeType,
)
from backend.core.analysis.confidence import ConfidenceResult
from backend.core.analysis.schemas import NormalizedFinding

logger = logging.getLogger("backend.analysis.explain")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_SEVERITY_EMOJI: dict = {          # kept out of production logs; markdown only
    "CRITICAL": "🔴",
    "HIGH":     "🟠",
    "MEDIUM":   "🟡",
    "LOW":      "🔵",
}


def _node_type_label(nt: EvidenceNodeType) -> str:
    return nt.value.replace("_", " ").title()


def _render_node_list(nodes: List[EvidenceNode], indent: int = 2) -> str:
    pad = " " * indent
    lines = []
    for n in nodes:
        loc = ""
        if n.file_path:
            loc = f" — `{n.file_path}`"
            if n.line_start:
                loc += f" L{n.line_start}"
                if n.line_end and n.line_end != n.line_start:
                    loc += f"–{n.line_end}"
        lines.append(f"{pad}- **[{_node_type_label(n.node_type)}]** `{n.label}`{loc}")
    return "\n".join(lines) if lines else f"{pad}*(none)*"


# ---------------------------------------------------------------------------
# Explanation data model
# ---------------------------------------------------------------------------

class ExplanationReport:
    """
    Structured explanation container.

    Attributes
    ----------
    markdown : Full developer-facing Markdown report.
    summary  : One-paragraph token-efficient summary for LLM contexts.
    forensic : JSON-serialisable dict for audit storage.
    """

    def __init__(
        self,
        finding:    NormalizedFinding,
        graph:      EvidenceGraph,
        confidence: ConfidenceResult,
        agents_participated: Optional[List[str]] = None,
        suppressed_alternatives: Optional[List[str]] = None,
    ):
        self.finding   = finding
        self.graph     = graph
        self.confidence= confidence
        self.agents    = agents_participated or []
        self.alts      = suppressed_alternatives or []

        self.markdown  = self._render_markdown()
        self.summary   = self._render_summary()
        self.forensic  = self._render_forensic()

    # ------------------------------------------------------------------
    # Renderers
    # ------------------------------------------------------------------

    def _render_markdown(self) -> str:
        f   = self.finding
        cr  = self.confidence
        g   = self.graph
        sev = f.severity
        emo = _SEVERITY_EMOJI.get(sev, "")

        # Categorise graph nodes by type for structured listing
        ast_nodes  = [n for n in g.nodes.values() if n.node_type == EvidenceNodeType.AST_NODE]
        cfg_nodes  = [n for n in g.nodes.values() if n.node_type == EvidenceNodeType.CFG_BLOCK]
        dfg_nodes  = [n for n in g.nodes.values() if n.node_type == EvidenceNodeType.DFG_VARIABLE]
        sym_nodes  = [n for n in g.nodes.values() if n.node_type == EvidenceNodeType.SYMBOL]
        doc_nodes  = [n for n in g.nodes.values() if n.node_type == EvidenceNodeType.DOCUMENTATION]
        file_nodes = [n for n in g.nodes.values() if n.node_type == EvidenceNodeType.FILE]

        sections: List[str] = []

        # Header
        sections.append(
            f"## {emo} [{sev}] Rule `{f.rule_id}`\n\n"
            f"> {f.description}"
        )

        # Location
        if f.file_path:
            loc_str = f"`{f.file_path}`"
            if f.line_number:
                loc_str += f" — line **{f.line_number}**"
            sections.append(f"### Location\n{loc_str}")

        # Flagged code
        sections.append(
            f"### Flagged Code\n```\n{f.line_text.strip()}\n```\n\n"
            f"**Evidence:** {f.evidence}"
        )

        # Confidence breakdown
        sections.append(
            "### Confidence Breakdown\n"
            f"| Signal | Contribution |\n"
            f"|--------|-------------|\n"
            f"| Static rule          | `{cr.static_contribution:.4f}` |\n"
            f"| Semantic validation  | `{cr.semantic_contribution:.4f}` |\n"
            f"| Agent consensus      | `{cr.consensus_contribution:.4f}` |\n"
            f"| Historical similarity| `{cr.history_contribution:.4f}` |\n"
            f"| Contradiction penalty| `-{cr.contradiction_penalty:.4f}` |\n"
            f"| Missing evidence pen.| `-{cr.evidence_penalty:.4f}` |\n"
            f"| **Final**            | **`{cr.final_score:.4f}`** |"
        )

        # Evidence nodes
        if ast_nodes or cfg_nodes or dfg_nodes or sym_nodes:
            ev_lines = []
            if ast_nodes:
                ev_lines.append("**AST Nodes:**\n" + _render_node_list(ast_nodes))
            if cfg_nodes:
                ev_lines.append("**CFG Blocks:**\n" + _render_node_list(cfg_nodes))
            if dfg_nodes:
                ev_lines.append("**DFG Variables:**\n" + _render_node_list(dfg_nodes))
            if sym_nodes:
                ev_lines.append("**Symbols:**\n" + _render_node_list(sym_nodes))
            sections.append("### Evidence Nodes\n" + "\n\n".join(ev_lines))

        # Source files
        if file_nodes:
            sections.append(
                "### Source Files\n" + _render_node_list(file_nodes)
            )

        # Agents
        if self.agents:
            ag_list = "\n".join(f"  - {a}" for a in self.agents)
            sections.append(f"### Participating Agents\n{ag_list}")

        # Assumptions
        low_conf_nodes = [
            n for n in g.nodes.values()
            if n.metadata.get("confidence", 1.0) < 0.5
        ]
        if low_conf_nodes:
            sections.append(
                "### Assumptions (Low-Confidence Nodes)\n"
                + _render_node_list(low_conf_nodes)
            )

        # Suppressed alternatives
        if self.alts:
            alt_list = "\n".join(f"  - {a}" for a in self.alts)
            sections.append(
                f"### Rejected Hypotheses\n"
                f"The following competing findings were evaluated and suppressed:\n{alt_list}"
            )

        # Remediation
        rem_text = f.remediation
        if doc_nodes:
            rem_text = doc_nodes[0].metadata.get("text", rem_text)
        sections.append(f"### Remediation\n{rem_text}")

        return "\n\n---\n\n".join(sections)

    def _render_summary(self) -> str:
        f  = self.finding
        cr = self.confidence
        loc = f.file_path or "unknown file"
        line = f" line {f.line_number}" if f.line_number else ""
        return (
            f"[{f.severity}] Rule `{f.rule_id}` flagged `{loc}`{line} with "
            f"confidence {cr.final_score:.2f}. {f.description} "
            f"Remediation: {f.remediation}"
        )

    def _render_forensic(self) -> dict:
        return {
            "finding_rule_id":  self.finding.rule_id,
            "severity":         self.finding.severity,
            "file_path":        self.finding.file_path,
            "line_number":      self.finding.line_number,
            "confidence":       self.confidence.model_dump(),
            "evidence_graph_id":self.graph.graph_id,
            "node_count":       len(self.graph.nodes),
            "edge_count":       len(self.graph.edges),
            "agents":           self.agents,
            "suppressed_alts":  self.alts,
        }


# ---------------------------------------------------------------------------
# Engine façade
# ---------------------------------------------------------------------------

class ExplainabilityEngine:
    """
    Generates ExplanationReports from (finding, graph, confidence) triples.

    This engine is intentionally stateless; it holds no mutable state so
    it is safe to share across threads and async tasks.
    """

    def explain(
        self,
        finding:    NormalizedFinding,
        graph:      EvidenceGraph,
        confidence: ConfidenceResult,
        agents_participated:     Optional[List[str]] = None,
        suppressed_alternatives: Optional[List[str]] = None,
    ) -> ExplanationReport:
        """
        Build and return an ExplanationReport.

        Parameters
        ----------
        finding    : The normalised finding to explain.
        graph      : Pre-built EvidenceGraph for this finding.
        confidence : Pre-computed ConfidenceResult.
        agents_participated     : Names of agents that contributed.
        suppressed_alternatives : rule_ids of competing hypotheses rejected.
        """
        report = ExplanationReport(
            finding=finding,
            graph=graph,
            confidence=confidence,
            agents_participated=agents_participated,
            suppressed_alternatives=suppressed_alternatives,
        )
        logger.info(
            "Explanation generated for rule '%s' — confidence %.4f — "
            "%d evidence nodes",
            finding.rule_id, confidence.final_score, len(graph.nodes),
        )
        return report
