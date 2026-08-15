"""
Evidence Graph Construction
===========================
Represents every analysis finding as a directed evidence graph, linking AST
nodes, CFG blocks, DFG variables, symbols, APIs, commits, and retrieved
documentation.  Every node carries a typed classification so downstream
consumers (Explainability, Confidence, Suppression) can reason structurally
rather than interpreting free-form text.
"""
import time
import uuid
import logging
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from backend.core.analysis.schemas import NormalizedFinding

logger = logging.getLogger("backend.analysis.evidence")


# ---------------------------------------------------------------------------
# Node & Edge Classification
# ---------------------------------------------------------------------------

class EvidenceNodeType(str, Enum):
    AST_NODE      = "ast_node"
    CFG_BLOCK     = "cfg_block"
    DFG_VARIABLE  = "dfg_variable"
    SYMBOL        = "symbol"
    FUNCTION      = "function"
    CLASS         = "class"
    API_ENDPOINT  = "api_endpoint"
    FILE          = "file"
    COMMIT        = "commit"
    DOCUMENTATION = "documentation"
    RETRIEVED_CTX = "retrieved_context"
    FINDING       = "finding"


class EvidenceEdgeType(str, Enum):
    DEPENDENCY          = "dependency"
    INVOCATION          = "invocation"
    CONTROL_FLOW        = "control_flow"
    DATA_FLOW           = "data_flow"
    SEMANTIC_SIMILARITY = "semantic_similarity"
    OWNERSHIP           = "ownership"
    INHERITANCE         = "inheritance"
    TEMPORAL_RELATION   = "temporal_relation"
    ARCHITECTURAL       = "architectural"


# ---------------------------------------------------------------------------
# Graph Primitives
# ---------------------------------------------------------------------------

class EvidenceNode(BaseModel):
    """A typed node inside an EvidenceGraph."""
    node_id: str       = Field(default_factory=lambda: str(uuid.uuid4()))
    node_type: EvidenceNodeType
    label: str                        # Human-readable identifier (symbol name, file path …)
    file_path: Optional[str]  = None
    line_start: Optional[int] = None
    line_end:   Optional[int] = None
    metadata: Dict[str, Any]  = Field(default_factory=dict)


class EvidenceEdge(BaseModel):
    """A directed relationship between two EvidenceNodes."""
    edge_id:    str = Field(default_factory=lambda: str(uuid.uuid4()))
    source_id:  str
    target_id:  str
    edge_type:  EvidenceEdgeType
    confidence: float = 1.0
    metadata: Dict[str, Any] = Field(default_factory=dict)


class EvidenceGraph(BaseModel):
    """
    Complete evidence graph produced for a single NormalizedFinding.

    Nodes represent code entities; edges represent structural or semantic
    relationships.  The graph is serialised to JSON for persistence, audit
    logging, and downstream consumption.
    """
    graph_id:    str = Field(default_factory=lambda: str(uuid.uuid4()))
    finding_id:  str
    created_at:  float = Field(default_factory=time.time)
    version:     int   = 1
    nodes: Dict[str, EvidenceNode] = Field(default_factory=dict)
    edges: List[EvidenceEdge]      = Field(default_factory=list)
    # Root node is always the finding itself
    root_node_id: Optional[str] = None

    # ------------------------------------------------------------------
    # Mutation helpers
    # ------------------------------------------------------------------

    def add_node(self, node: EvidenceNode) -> str:
        self.nodes[node.node_id] = node
        logger.debug("EvidenceGraph[%s] added node '%s' (%s)",
                     self.graph_id, node.label, node.node_type)
        return node.node_id

    def add_edge(self, edge: EvidenceEdge) -> None:
        if edge.source_id not in self.nodes:
            raise ValueError(f"Source node '{edge.source_id}' not in graph.")
        if edge.target_id not in self.nodes:
            raise ValueError(f"Target node '{edge.target_id}' not in graph.")
        self.edges.append(edge)

    def get_neighbours(self, node_id: str) -> List[EvidenceNode]:
        ids = {e.target_id for e in self.edges if e.source_id == node_id}
        ids |= {e.source_id for e in self.edges if e.target_id == node_id}
        return [self.nodes[i] for i in ids if i in self.nodes]


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

class EvidenceGraphBuilder:
    """
    Constructs an EvidenceGraph from a NormalizedFinding.

    Strategy
    --------
    1. Create a root FINDING node.
    2. Create a FILE node for the source file and link it (OWNERSHIP).
    3. If line information is present, create an AST_NODE for the flagged
       statement and link it (CONTROL_FLOW from FILE → AST).
    4. Parse the evidence string for additional referenced symbols and attach
       them as SYMBOL nodes linked by DATA_FLOW.
    5. Attach a DOCUMENTATION node carrying the remediation text.
    """

    def build(self, finding: NormalizedFinding) -> EvidenceGraph:
        graph = EvidenceGraph(finding_id=finding.rule_id)

        # --- Root finding node -------------------------------------------
        root = EvidenceNode(
            node_type=EvidenceNodeType.FINDING,
            label=f"[{finding.rule_id}] {finding.description[:60]}",
            metadata={
                "severity": finding.severity,
                "confidence": finding.static_confidence,
                "source": finding.source,
            },
        )
        graph.add_node(root)
        graph.root_node_id = root.node_id

        # --- AST node — always created from flagged line_text ------------
        # This guarantees ≥ 1 code-entity node for Stage 1 validation even
        # when file_path is not available (e.g. bare code-string analysis).
        ast_node = EvidenceNode(
            node_type=EvidenceNodeType.AST_NODE,
            label=finding.line_text.strip() or finding.evidence,
            file_path=finding.file_path,
            line_start=finding.line_number,
            line_end=finding.line_number,
            metadata={"evidence_text": finding.evidence},
        )
        graph.add_node(ast_node)
        graph.add_edge(EvidenceEdge(
            source_id=root.node_id,
            target_id=ast_node.node_id,
            edge_type=EvidenceEdgeType.DEPENDENCY,
            confidence=finding.static_confidence,
        ))

        # --- Source file node (only when file_path is known) -------------
        if finding.file_path:
            file_node = EvidenceNode(
                node_type=EvidenceNodeType.FILE,
                label=finding.file_path,
                file_path=finding.file_path,
            )
            graph.add_node(file_node)
            graph.add_edge(EvidenceEdge(
                source_id=root.node_id,
                target_id=file_node.node_id,
                edge_type=EvidenceEdgeType.OWNERSHIP,
            ))
            graph.add_edge(EvidenceEdge(
                source_id=file_node.node_id,
                target_id=ast_node.node_id,
                edge_type=EvidenceEdgeType.CONTROL_FLOW,
            ))

        # --- Remediation documentation node ------------------------------
        doc_node = EvidenceNode(
            node_type=EvidenceNodeType.DOCUMENTATION,
            label="Remediation",
            metadata={"text": finding.remediation},
        )
        graph.add_node(doc_node)
        graph.add_edge(EvidenceEdge(
            source_id=root.node_id,
            target_id=doc_node.node_id,
            edge_type=EvidenceEdgeType.SEMANTIC_SIMILARITY,
            confidence=1.0,
        ))

        logger.info("EvidenceGraph built for rule '%s': %d nodes, %d edges.",
                    finding.rule_id, len(graph.nodes), len(graph.edges))
        return graph

