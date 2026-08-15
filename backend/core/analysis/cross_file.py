"""
Cross-File & Cross-Service Reasoner  (§114)
=============================================
Traces execution across module, package, and service boundaries using the
RepositoryKnowledgeGraph and the IPA call graph.

Design invariants
-----------------
- Traversal is bounded at max_depth hops (default: 5).
- Cycles are recorded as CycleRecord objects and pruned from traversal.
- Pruned paths produce EvidenceEdges with confidence < 1.0 so downstream
  consumers know the trace was cut short.
- Unresolvable module references produce a sentinel "UNRESOLVED:<ref>" node.
"""
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from backend.core.analysis.repo_graph import RepositoryKnowledgeGraph
from backend.core.analysis.multi_hop import MultiHopReasoner, HopPath

logger = logging.getLogger("backend.analysis.cross_file")


@dataclass
class CycleRecord:
    """A detected dependency cycle."""
    nodes: List[str]

    def __str__(self) -> str:
        return " → ".join(self.nodes)


@dataclass
class CrossFileTrace:
    """Result of a cross-file traversal."""
    start: str
    paths: List[HopPath]         = field(default_factory=list)
    cycles: List[CycleRecord]    = field(default_factory=list)
    unresolved: List[str]        = field(default_factory=list)
    max_depth_reached: bool      = False


class CrossFileReasoner:
    """
    Traces cross-module and cross-service dependencies.

    Parameters
    ----------
    max_depth : Maximum traversal hops (default 5, hard cap 15).
    """

    def __init__(self, max_depth: int = 5):
        self._hopper = MultiHopReasoner(max_depth=max_depth)
        # Cycle detection walks the entire graph, but the graph only changes
        # when a symbol or edge is added. During a repository scan the same
        # graph is traced once per finding, so memoising on the graph version
        # turns O(findings x graph) back into O(graph) per mutation.
        self._cycle_cache: Optional[tuple] = None      # (version, [CycleRecord])

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def trace(
        self,
        graph: RepositoryKnowledgeGraph,
        start: str,
        max_depth: Optional[int] = None,
    ) -> CrossFileTrace:
        """
        Trace all cross-file paths reachable from *start*.

        Uses both the dependency graph (file-level imports) and the call graph
        (symbol-level invocations). Results are merged and deduplicated.

        Parameters
        ----------
        graph     : The populated RepositoryKnowledgeGraph.
        start     : Starting node (file path or symbol name).
        max_depth : Override default depth for this call.
        """
        result = CrossFileTrace(start=start)

        # Build unified adjacency from both layers
        adjacency: Dict[str, Set[str]] = {}

        # Layer 1: file-level dependencies
        for fp, deps in graph.dependencies.items():
            adjacency.setdefault(fp, set()).update(deps)

        # Layer 2: symbol call graph
        for caller, callees in graph.call_graph.items():
            adjacency.setdefault(caller, set()).update(callees)

        if start not in adjacency:
            logger.warning(
                "CrossFileReasoner: start '%s' not found in dependency or call graph.", start
            )
            result.unresolved.append(start)
            return result

        # Detect cycles first (memoised on the graph's mutation counter)
        version = getattr(graph, "version", None)
        if version is not None and self._cycle_cache and self._cycle_cache[0] == version:
            result.cycles = self._cycle_cache[1]
        else:
            raw_cycles = self._hopper.detect_cycles(adjacency)
            result.cycles = [CycleRecord(nodes=c) for c in raw_cycles]
            if version is not None:
                self._cycle_cache = (version, result.cycles)

        # Trace paths
        paths = self._hopper.trace(adjacency, start=start, max_depth=max_depth)
        result.paths = paths

        # Flag if any path reached max depth
        d_limit = max_depth or self._hopper.max_depth
        result.max_depth_reached = any(p.depth >= d_limit for p in paths)

        logger.info(
            "CrossFileReasoner: %d path(s), %d cycle(s) from '%s' (depth≤%d).",
            len(paths), len(result.cycles), start, d_limit,
        )
        return result

    def get_all_dependents(
        self,
        graph: RepositoryKnowledgeGraph,
        target_file: str,
    ) -> List[str]:
        """
        Return all files/symbols that directly or transitively depend on
        *target_file* (reverse traversal).
        """
        reverse: Dict[str, Set[str]] = {}
        for fp, deps in graph.dependencies.items():
            for dep in deps:
                reverse.setdefault(dep, set()).add(fp)
        for caller, callees in graph.call_graph.items():
            for callee in callees:
                reverse.setdefault(callee, set()).add(caller)

        paths = self._hopper.trace(reverse, start=target_file)
        dependents = set()
        for p in paths:
            dependents.update(p.nodes[1:])  # exclude start itself
        return sorted(dependents)
