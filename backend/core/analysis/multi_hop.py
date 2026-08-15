"""
Multi-Hop Reasoning Engine  (§117)
====================================
Traces dependency and semantic relationships across arbitrary graph depth over
a RepositoryKnowledgeGraph while bounding computational complexity.

Design invariants
-----------------
- BFS and DFS strategies are both supported; BFS is the default.
- Neighbours are sorted by node name before enqueuing for deterministic ordering.
- Each hop multiplies accumulated confidence by the outgoing edge weight; paths
  whose accumulated confidence drops below `confidence_threshold` are pruned.
- Cycles are detected by tracking the current path set; back-edges are skipped.
- Hard depth cap: default 5, configurable up to 15.
"""
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional, Set

logger = logging.getLogger("backend.analysis.multi_hop")

# Maximum allowed depth to prevent misconfiguration
_ABSOLUTE_MAX_DEPTH = 15


@dataclass
class HopPath:
    """A single traced path through the graph."""
    nodes: List[str]               # ordered node identifiers from start to end
    accumulated_confidence: float  # product of all edge confidences along the path
    depth: int                     # len(nodes) - 1

    @property
    def start(self) -> str:
        return self.nodes[0]

    @property
    def end(self) -> str:
        return self.nodes[-1]


@dataclass
class _QueueItem:
    node:       str
    path:       List[str]
    confidence: float


class MultiHopReasoner:
    """
    Traces multi-hop paths through a RepositoryKnowledgeGraph.

    Parameters
    ----------
    max_depth          : Maximum hop count (default 5, hard cap 15).
    confidence_threshold : Prune paths whose accumulated confidence falls below
                           this value (default 0.05).
    """

    def __init__(
        self,
        max_depth: int = 5,
        confidence_threshold: float = 0.05,
    ):
        self.max_depth = min(max_depth, _ABSOLUTE_MAX_DEPTH)
        self.confidence_threshold = confidence_threshold

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def trace(
        self,
        adjacency: Dict[str, Set[str]],
        start: str,
        max_depth: Optional[int] = None,
        strategy: Literal["bfs", "dfs"] = "bfs",
        edge_confidence: Optional[Dict[str, float]] = None,
    ) -> List[HopPath]:
        """
        Enumerate all paths reachable from *start* within *max_depth* hops.

        Parameters
        ----------
        adjacency       : ``{node_id: {neighbour_id, …}}`` mapping.
        start           : Starting node identifier.
        max_depth       : Override instance default for this call.
        strategy        : "bfs" (breadth-first, default) or "dfs" (depth-first).
        edge_confidence : Optional ``{neighbour_id: float}`` per-edge weights.
                          Defaults to 1.0 for all edges.

        Returns
        -------
        List[HopPath] sorted by accumulated_confidence descending.
        """
        depth_limit = min(max_depth or self.max_depth, _ABSOLUTE_MAX_DEPTH)
        ec = edge_confidence or {}

        paths: List[HopPath] = []

        if start not in adjacency:
            logger.debug("MultiHopReasoner: start node '%s' not in adjacency map.", start)
            return paths

        # Use deque as queue (BFS: popleft) or stack (DFS: pop)
        queue: deque = deque()
        queue.append(_QueueItem(node=start, path=[start], confidence=1.0))

        while queue:
            item = queue.popleft() if strategy == "bfs" else queue.pop()

            current_depth = len(item.path) - 1
            if current_depth >= depth_limit:
                paths.append(HopPath(
                    nodes=list(item.path),
                    accumulated_confidence=item.confidence,
                    depth=current_depth,
                ))
                continue

            neighbours = sorted(adjacency.get(item.node, set()))  # deterministic order
            expanded = False
            for neighbour in neighbours:
                if neighbour in item.path:           # cycle detection
                    continue
                edge_w = ec.get(neighbour, 1.0)
                new_conf = item.confidence * edge_w
                if new_conf < self.confidence_threshold:  # pruning
                    continue
                expanded = True
                queue.append(_QueueItem(
                    node=neighbour,
                    path=item.path + [neighbour],
                    confidence=new_conf,
                ))

            if not expanded and len(item.path) > 1:
                # Leaf node or fully pruned — record the path
                paths.append(HopPath(
                    nodes=list(item.path),
                    accumulated_confidence=item.confidence,
                    depth=len(item.path) - 1,
                ))

        paths.sort(key=lambda p: p.accumulated_confidence, reverse=True)
        logger.debug(
            "MultiHopReasoner traced %d path(s) from '%s' (depth≤%d, strategy=%s).",
            len(paths), start, depth_limit, strategy,
        )
        return paths

    def detect_cycles(self, adjacency: Dict[str, Set[str]]) -> List[List[str]]:
        """
        Return all cycles in *adjacency* using DFS back-edge detection.
        Each cycle is returned as the ordered list of node IDs forming the loop.
        """
        visited: Set[str] = set()
        rec_stack: Set[str] = set()
        cycles: List[List[str]] = []

        def _dfs(node: str, path: List[str]) -> None:
            visited.add(node)
            rec_stack.add(node)
            for neighbour in sorted(adjacency.get(node, set())):
                if neighbour not in visited:
                    _dfs(neighbour, path + [neighbour])
                elif neighbour in rec_stack:
                    # Cycle found — extract the loop segment
                    idx = path.index(neighbour)
                    cycles.append(path[idx:] + [neighbour])
            rec_stack.discard(node)

        for n in sorted(adjacency.keys()):
            if n not in visited:
                _dfs(n, [n])

        logger.info("MultiHopReasoner detected %d cycle(s).", len(cycles))
        return cycles
