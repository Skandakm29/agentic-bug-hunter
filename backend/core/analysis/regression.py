"""
Regression Impact Analysis Engine  (§123)
==========================================
Predicts the downstream impact of applying a RemediationPlan across the full
codebase before any implementation occurs.

Strategy
--------
1. Collect all CodeSpan targets from accepted strategies.
2. Resolve each target span to a symbol name in the RepositoryKnowledgeGraph.
3. Perform forward IPA traversal from each symbol: find all transitively
   reachable callers using CrossFileReasoner.get_all_dependents().
4. For each affected component, compute regression_risk:
       regression_risk = reach_fraction × dependency_centrality × (1 - test_coverage_estimate)
5. Sort components by regression_risk descending.
6. Emit API-compatibility, security, and performance flags based on component type.
"""
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from backend.core.analysis.remediation import RemediationPlan
from backend.core.analysis.repo_graph import RepositoryKnowledgeGraph
from backend.core.analysis.cross_file import CrossFileReasoner

logger = logging.getLogger("backend.analysis.regression")


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class AffectedComponent:
    """A component transitively affected by a proposed remediation."""
    name:               str
    regression_risk:    float
    reach_count:        int      # number of paths that touch this component
    dependency_centrality: float # fraction of all known symbols that call this
    test_coverage_estimate: float = 0.5  # default when no coverage data is available
    api_compatibility_flag: bool  = False
    security_flag:          bool  = False
    performance_flag:       bool  = False


@dataclass
class RegressionImpactReport:
    """Full regression impact analysis output."""
    finding_rule_id:      str
    # file:line references of planned changes — populated by RegressionAnalyzer.analyze()
    modified_spans:       List[str] = field(default_factory=list)
    affected_components:  List[AffectedComponent] = field(default_factory=list)
    api_compat_verdict:   str  = "UNKNOWN"   # "SAFE" | "AT_RISK" | "UNKNOWN"
    security_flags:       List[str] = field(default_factory=list)
    performance_flags:    List[str] = field(default_factory=list)
    total_reachable:      int  = 0


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class RegressionAnalyzer:
    """
    Analyzes the regression impact of accepting a RemediationPlan.

    Parameters
    ----------
    test_coverage_map : Optional dict mapping symbol/file name → coverage [0,1].
    """

    # Keyword patterns used to flag API, security, and perf-sensitive symbols
    _API_KEYWORDS  = ("api", "endpoint", "router", "controller", "view", "handler")
    _SEC_KEYWORDS  = ("auth", "token", "crypt", "password", "secret", "permission")
    _PERF_KEYWORDS = ("cache", "query", "db", "index", "batch", "loop", "perf")

    def __init__(
        self,
        test_coverage_map: Optional[Dict[str, float]] = None,
        max_traversal_depth: int = 5,
    ):
        self._coverage     = test_coverage_map or {}
        self._cross_file   = CrossFileReasoner(max_depth=max_traversal_depth)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(
        self,
        plan:  RemediationPlan,
        graph: RepositoryKnowledgeGraph,
    ) -> RegressionImpactReport:
        """Return a RegressionImpactReport for the given RemediationPlan."""
        report = RegressionImpactReport(finding_rule_id=plan.finding_rule_id)

        # Collect modification targets from accepted strategies
        modified_nodes: List[str] = []
        for acc in plan.accepted:
            for span in acc.strategy.target_spans:
                ref = f"{span.file_path}:{span.start_line}" if span.start_line else span.file_path
                if ref:
                    modified_nodes.append(ref)
                # Also add symbol-level if available
                if span.symbol_name:
                    modified_nodes.append(span.symbol_name)
            # Always include affected_components
            modified_nodes.extend(acc.strategy.affected_components)

        report.modified_spans = sorted(set(modified_nodes))

        # Forward traversal to find all dependents
        all_dependents: Dict[str, int] = {}  # name → reach_count
        for node in modified_nodes:
            deps = self._cross_file.get_all_dependents(graph, node)
            for d in deps:
                all_dependents[d] = all_dependents.get(d, 0) + 1

        total_symbols = max(len(graph.symbols), 1)
        report.total_reachable = len(all_dependents)

        # Build AffectedComponent list
        components: List[AffectedComponent] = []
        for comp_name, reach in all_dependents.items():
            centrality = min(reach / total_symbols, 1.0)
            coverage   = self._coverage.get(comp_name, 0.5)
            risk       = round(
                (reach / max(report.total_reachable, 1))
                * centrality
                * (1.0 - coverage),
                4,
            )
            lower = comp_name.lower()
            api_flag  = any(k in lower for k in self._API_KEYWORDS)
            sec_flag  = any(k in lower for k in self._SEC_KEYWORDS)
            perf_flag = any(k in lower for k in self._PERF_KEYWORDS)

            components.append(AffectedComponent(
                name=comp_name,
                regression_risk=risk,
                reach_count=reach,
                dependency_centrality=centrality,
                test_coverage_estimate=coverage,
                api_compatibility_flag=api_flag,
                security_flag=sec_flag,
                performance_flag=perf_flag,
            ))

        components.sort(key=lambda c: c.regression_risk, reverse=True)
        report.affected_components = components

        # Roll-up flags
        api_at_risk = any(c.api_compatibility_flag and c.regression_risk > 0.30
                          for c in components)
        report.api_compat_verdict = "AT_RISK" if api_at_risk else "SAFE"

        report.security_flags = [
            c.name for c in components if c.security_flag and c.regression_risk > 0.20
        ]
        report.performance_flags = [
            c.name for c in components if c.performance_flag and c.regression_risk > 0.20
        ]

        logger.info(
            "RegressionAnalyzer: %d affected component(s), api=%s, sec_flags=%d for rule '%s'.",
            len(components), report.api_compat_verdict,
            len(report.security_flags), plan.finding_rule_id,
        )
        return report
