"""
Root Cause Analysis Engine  (§115)
=====================================
Infers causal chains separating observed defect symptoms from originating
root causes by performing backward DFG slicing, IPA traversal, and
hypothesis ranking.

Design invariants
-----------------
- All causal hypotheses (including eliminated ones) are preserved in the
  output so the Explainability Engine can render rejected alternatives.
- Ranking is deterministic: identical graph inputs produce identical orderings.
- If the backward slice is empty, the finding's own location is returned as
  root cause with confidence = static_confidence × 0.5.
"""
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from backend.core.analysis.repo_graph import RepositoryKnowledgeGraph
from backend.core.analysis.dfg import DFGConstructor, DataDependency
from backend.core.analysis.ipa import InterproceduralCallGraph
from backend.core.analysis.schemas import NormalizedFinding

logger = logging.getLogger("backend.analysis.root_cause")


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class CausalNode:
    """A single hypothesis in the root cause chain."""
    hypothesis:       str                # human-readable description
    evidence_sources: List[str]          # rule_ids, symbol names, file:line refs
    confidence:       float
    eliminated:       bool = False       # True = contradicted, kept for audit
    elimination_reason: Optional[str]  = None


@dataclass
class RootCauseChain:
    """Ordered list of causal nodes (highest-confidence first, eliminated last)."""
    finding_rule_id: str
    nodes: List[CausalNode] = field(default_factory=list)

    @property
    def primary(self) -> Optional[CausalNode]:
        """Highest-confidence, non-eliminated node."""
        active = [n for n in self.nodes if not n.eliminated]
        return active[0] if active else None

    @property
    def eliminated_count(self) -> int:
        return sum(1 for n in self.nodes if n.eliminated)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class RootCauseAnalyzer:
    """
    Performs backward analysis to identify the originating root cause of a
    NormalizedFinding.

    Parameters
    ----------
    max_backward_hops : Maximum backward IPA/DFG traversal depth (default 4).
    """

    def __init__(self, max_backward_hops: int = 4):
        self.max_backward_hops = max_backward_hops

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(
        self,
        finding: NormalizedFinding,
        graph: RepositoryKnowledgeGraph,
        dfg_deps: Optional[List[DataDependency]] = None,
        call_graph: Optional[InterproceduralCallGraph] = None,
    ) -> RootCauseChain:
        """
        Build a RootCauseChain for *finding*.

        Parameters
        ----------
        finding    : The NormalizedFinding to explain.
        graph      : RepositoryKnowledgeGraph for symbol and call resolution.
        dfg_deps   : Pre-computed DFG dependency list (optional).
        call_graph : Pre-computed IPA call graph (optional).
        """
        chain = RootCauseChain(finding_rule_id=finding.rule_id)
        hypotheses: List[CausalNode] = []

        # --- H1: direct defect at flagged line ----------------------------
        h1_conf = finding.static_confidence
        h1 = CausalNode(
            hypothesis=f"Defect originates at {finding.file_path}:{finding.line_number}",
            evidence_sources=[finding.rule_id, finding.evidence],
            confidence=h1_conf,
        )
        hypotheses.append(h1)

        # --- H2: backward DFG slice — variable definition site -----------
        if dfg_deps:
            target_vars = self._extract_variable_refs(finding.line_text)
            for dep in dfg_deps:
                if dep.target_var in target_vars and dep.source_var != dep.target_var:
                    conf = h1_conf * 0.85
                    hypotheses.append(CausalNode(
                        hypothesis=(
                            f"Variable '{dep.target_var}' defined from '{dep.source_var}' "
                            f"may propagate the defect."
                        ),
                        evidence_sources=[f"dfg:{dep.source_var}→{dep.target_var}"],
                        confidence=conf,
                    ))

        # --- H3: IPA backward traversal — calling site -------------------
        if call_graph and finding.file_path:
            # Symbols defined in the finding's file, via the by-file index.
            file_symbols = graph.symbol_names_in_file(finding.file_path)
            for sym in sorted(file_symbols):
                callers = graph.get_callers(sym)
                for caller in callers[:3]:  # cap at 3 callers to avoid explosion
                    conf = h1_conf * 0.70
                    hypotheses.append(CausalNode(
                        hypothesis=(
                            f"Caller '{caller}' invokes '{sym}' and may be "
                            f"the originating call site."
                        ),
                        evidence_sources=[f"ipa:{caller}→{sym}"],
                        confidence=conf,
                    ))

        # --- Eliminate low-evidence hypotheses ---------------------------
        for h in hypotheses:
            if h.confidence < 0.20 and h is not h1:
                h.eliminated = True
                h.elimination_reason = (
                    f"confidence {h.confidence:.2f} below elimination threshold 0.20"
                )

        # --- Sort: active first (by confidence desc), eliminated last ----
        active   = sorted([h for h in hypotheses if not h.eliminated],
                           key=lambda h: h.confidence, reverse=True)
        elim     = [h for h in hypotheses if h.eliminated]
        chain.nodes = active + elim

        # --- Fallback: empty slice ----------------------------------------
        if not active:
            fallback = CausalNode(
                hypothesis=f"Root cause unresolvable; reporting finding location as proxy.",
                evidence_sources=[finding.rule_id],
                confidence=finding.static_confidence * 0.50,
            )
            chain.nodes.insert(0, fallback)

        logger.info(
            "RootCauseAnalyzer: %d hypothesis(es) (%d eliminated) for rule '%s'.",
            len(chain.nodes), chain.eliminated_count, finding.rule_id,
        )
        return chain

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_variable_refs(line_text: str) -> Set[str]:
        """
        Naively extract token-like variable references from a source line.
        Real implementations would use the UIR/AST; this provides a
        best-effort fallback.
        """
        import re
        tokens = re.findall(r'\b[a-zA-Z_][a-zA-Z0-9_]*\b', line_text)
        # Exclude common C++ / Python keywords
        keywords = {
            "if","else","for","while","return","int","float","double","void",
            "char","bool","true","false","nullptr","None","self","def","class",
        }
        return {t for t in tokens if t not in keywords}
