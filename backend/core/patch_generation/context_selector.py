"""
Context Selector  (Phase 3D.1)
================================
Constructs a token-bounded ``ContextWindow`` from the full repository by
intelligently selecting only the code and metadata the LLM needs to
generate a high-quality patch.

Selection strategy
------------------
1. **Target region** — Extract the N lines around the bug location from the
   target file.  Default radius is ±40 lines, clipped to file boundaries.
2. **Neighboring functions** — Extract bodies of other top-level functions
   within the same file (up to 3 closest) to give style context.
3. **Referenced classes** — Look up any class symbols used in the target
   function via the RepositoryKnowledgeGraph.
4. **Required includes** — All ``#include`` directives from the target file.
5. **Inheritance hierarchy** — Parent class declarations for the target class.
6. **Call graph excerpt** — Callers and callees of the target symbol (depth=1).
7. **Data flow excerpt** — DFG dependency chains touching affected variables.
8. **CFG nodes** — Basic block statements from the block containing the bug.
9. **RAG documents** — Documentation nodes from the EvidenceGraph.

The selector enforces a character budget (``config.context_window``) and
truncates lower-priority sections first.

Design invariants
-----------------
- The target region is NEVER truncated.
- Sections are added in priority order; lower-priority sections are omitted
  if the budget would be exceeded.
- ``ContextOverflowError`` is raised only if the target region alone exceeds
  the budget (i.e., the budget is too small for any useful context).
"""
from __future__ import annotations

import logging
import re
from typing import List, Optional

from backend.core.analysis.schemas import NormalizedFinding
from backend.core.analysis.evidence import EvidenceGraph, EvidenceNodeType
from backend.core.analysis.repo_graph import RepositoryKnowledgeGraph
from backend.core.analysis.dfg import DataDependency
from backend.core.analysis.cfg import CFGGraph
from backend.core.patch_generation.exceptions import ContextOverflowError
from backend.core.patch_generation.patch_models import ContextWindow, PatchGenerationConfig

logger = logging.getLogger("backend.patch_generation.context_selector")

# Radius (lines) around the bug for the target region
_DEFAULT_LINE_RADIUS = 40
# Max number of neighboring functions to include
_MAX_NEIGHBOR_FUNCS  = 3
# Max chars per single neighboring function
_MAX_FUNC_CHARS      = 800


class ContextSelector:
    """
    Constructs a token-bounded ``ContextWindow`` for patch generation.

    Parameters
    ----------
    config : ``PatchGenerationConfig`` providing the character budget.
    line_radius : Lines above/below the bug location to include.
    """

    def __init__(
        self,
        config: PatchGenerationConfig,
        line_radius: int = _DEFAULT_LINE_RADIUS,
    ) -> None:
        self._config      = config
        self._line_radius = line_radius

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def select(
        self,
        finding:    NormalizedFinding,
        code:       str,
        graph:      Optional[RepositoryKnowledgeGraph] = None,
        evidence:   Optional[EvidenceGraph]            = None,
        dfg_deps:   Optional[List[DataDependency]]     = None,
        cfg_graph:  Optional[CFGGraph]                 = None,
    ) -> ContextWindow:
        """
        Build and return a ``ContextWindow`` within the configured budget.

        Parameters
        ----------
        finding   : The NormalizedFinding containing location metadata.
        code      : Full source code string of the target file.
        graph     : RepositoryKnowledgeGraph for symbol/class resolution.
        evidence  : EvidenceGraph for RAG document extraction.
        dfg_deps  : Pre-computed DFG dependencies (for data flow excerpt).
        cfg_graph : Pre-computed CFG (for basic block extraction).
        """
        budget = self._config.context_window
        window = ContextWindow(
            target_file_path=finding.file_path,
        )

        # Priority 1: target region (never skipped)
        target_content, start_line, end_line = self._extract_target_region(
            code, finding.line_number
        )
        if len(target_content) > budget:
            raise ContextOverflowError(
                estimated_tokens=len(target_content),
                budget=budget,
                details={"file_path": finding.file_path},
            )
        window.target_file_content = target_content
        window.target_start_line   = start_line
        window.target_end_line     = end_line
        remaining = budget - len(target_content)

        # Priority 2: required includes
        includes = self._extract_includes(code)
        inc_str  = "\n".join(includes)
        if len(inc_str) <= remaining:
            window.required_includes = includes
            remaining -= len(inc_str)

        # Priority 3: neighboring functions
        if remaining > 0:
            neighbors = self._extract_neighboring_functions(
                code, finding.line_number, remaining
            )
            window.neighboring_functions = neighbors
            remaining -= sum(len(n) for n in neighbors)

        # Priority 4: referenced classes from graph
        if graph and remaining > 0:
            classes = self._extract_referenced_classes(
                finding, graph, remaining
            )
            window.referenced_classes = classes
            remaining -= sum(len(c) for c in classes)

        # Priority 5: inheritance hierarchy
        if graph and remaining > 0:
            hierarchy = self._extract_inheritance(finding, graph, remaining)
            window.inheritance_hierarchy = hierarchy
            remaining -= sum(len(h) for h in hierarchy)

        # Priority 6: call graph excerpt
        if graph and remaining > 0:
            cg_excerpt = self._build_call_graph_excerpt(finding, graph)
            if len(cg_excerpt) <= remaining:
                window.call_graph_excerpt = cg_excerpt
                remaining -= len(cg_excerpt)

        # Priority 7: data flow excerpt
        if dfg_deps and remaining > 0:
            df_excerpt = self._build_data_flow_excerpt(
                finding, dfg_deps, remaining
            )
            if len(df_excerpt) <= remaining:
                window.data_flow_excerpt = df_excerpt
                remaining -= len(df_excerpt)

        # Priority 8: CFG nodes
        if cfg_graph and remaining > 0:
            cfg_stmts = self._extract_cfg_nodes(cfg_graph, remaining)
            window.cfg_nodes = cfg_stmts
            remaining -= sum(len(s) for s in cfg_stmts)

        # Priority 9: RAG documents from evidence graph
        if evidence and remaining > 0:
            rag_docs = self._extract_rag_documents(evidence, remaining)
            window.rag_documents = rag_docs

        # Compute final char count
        window.estimated_char_count = self._estimate_chars(window)

        logger.info(
            "ContextSelector: budget=%d chars, used=%d chars, "
            "target_lines=%d-%d, includes=%d, neighbors=%d, classes=%d, "
            "rag_docs=%d.",
            budget,
            window.estimated_char_count,
            start_line or 0,
            end_line   or 0,
            len(window.required_includes),
            len(window.neighboring_functions),
            len(window.referenced_classes),
            len(window.rag_documents),
        )
        return window

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _extract_target_region(
        self,
        code:        str,
        line_number: Optional[int],
    ) -> tuple[str, Optional[int], Optional[int]]:
        """Return (code_region, start_line, end_line) around the bug."""
        lines = code.splitlines()
        if not lines:
            return code, 1, 1

        if line_number is None:
            # No location: return whole file (truncated to 200 lines)
            truncated = "\n".join(lines[:200])
            return truncated, 1, min(200, len(lines))

        # 1-indexed line number → 0-indexed
        idx  = max(0, line_number - 1)
        lo   = max(0,            idx - self._line_radius)
        hi   = min(len(lines),   idx + self._line_radius + 1)

        region = "\n".join(lines[lo:hi])
        return region, lo + 1, hi  # back to 1-indexed

    @staticmethod
    def _extract_includes(code: str) -> List[str]:
        """Return all #include directives in the file."""
        pattern = re.compile(r'^\s*#\s*include\s*[<"].*[>"]', re.MULTILINE)
        return pattern.findall(code)

    def _extract_neighboring_functions(
        self,
        code:        str,
        line_number: Optional[int],
        budget:      int,
    ) -> List[str]:
        """
        Heuristically extract top-level function bodies adjacent to the bug.
        Uses a simple brace-counting heuristic suitable for C/C++.
        """
        # Regex to find function definitions (simplified; handles most cases)
        func_re = re.compile(
            r'(?:^|\n)'
            r'(?:(?:inline|static|virtual|explicit|constexpr|auto|[\w:*&<>\s]+)\s+)'
            r'(\w+)\s*\([^)]*\)\s*(?:const\s*)?(?:noexcept\s*)?(?:override\s*)?'
            r'(?:=\s*(?:default|delete)\s*;|->.*?\s*)?\{',
        )
        lines  = code.splitlines()
        result: List[str] = []
        total  = 0

        matches = list(func_re.finditer(code))
        for i, m in enumerate(matches):
            start_char = m.start()
            # Determine function line number
            func_line = code[:start_char].count('\n') + 1
            if line_number and abs(func_line - line_number) < 5:
                continue  # skip the function containing the bug itself

            # Extract body up to next function or eof
            end_char = matches[i + 1].start() if i + 1 < len(matches) else len(code)
            body     = code[start_char:end_char].strip()

            if len(body) > _MAX_FUNC_CHARS:
                body = body[:_MAX_FUNC_CHARS] + "\n    // ... [truncated]"

            if total + len(body) > budget:
                break

            result.append(body)
            total += len(body)

            if len(result) >= _MAX_NEIGHBOR_FUNCS:
                break

        return result

    @staticmethod
    def _extract_referenced_classes(
        finding: NormalizedFinding,
        graph:   RepositoryKnowledgeGraph,
        budget:  int,
    ) -> List[str]:
        """
        Find class symbol names referenced in the evidence text and return
        their declaration stubs from the repository graph.
        """
        evidence_text = finding.evidence + " " + finding.line_text
        tokens = set(re.findall(r'\b[A-Z][A-Za-z0-9_]+\b', evidence_text))
        result: List[str] = []
        total  = 0

        for sym in graph.symbols.values():
            if sym.symbol_type == "class" and sym.name in tokens:
                stub = f"class {sym.name}  // defined at {sym.file_path}:{sym.line_number}"
                if total + len(stub) + 2 > budget:
                    break
                result.append(stub)
                total += len(stub) + 2

        return result

    @staticmethod
    def _extract_inheritance(
        finding: NormalizedFinding,
        graph:   RepositoryKnowledgeGraph,
        budget:  int,
    ) -> List[str]:
        """
        Identify base classes of the class containing the bug and return
        their declaration stubs.
        """
        evidence_text = finding.evidence + " " + finding.line_text
        class_names   = set(re.findall(r'\b[A-Z][A-Za-z0-9_]+\b', evidence_text))
        result: List[str] = []
        total  = 0

        for sym in graph.symbols.values():
            if sym.symbol_type == "class" and sym.name in class_names:
                for dep in sym.dependencies:
                    stub = f"// {sym.name} inherits from {dep}"
                    if total + len(stub) + 2 > budget:
                        return result
                    result.append(stub)
                    total += len(stub) + 2

        return result

    @staticmethod
    def _build_call_graph_excerpt(
        finding: NormalizedFinding,
        graph:   RepositoryKnowledgeGraph,
    ) -> str:
        """Build a textual call graph excerpt for symbols in the target file."""
        if not finding.file_path:
            return ""

        file_symbols = [
            s.name for s in graph.symbols.values()
            if s.file_path == finding.file_path and s.symbol_type == "function"
        ]
        lines: List[str] = []
        for sym in file_symbols[:5]:  # cap at 5 to control size
            callees = list(graph.call_graph.get(sym, []))[:4]
            callers = graph.get_callers(sym)[:4]
            if callees:
                lines.append(f"  {sym}() → {', '.join(callees)}")
            if callers:
                lines.append(f"  ← {sym}() called by: {', '.join(callers)}")
        return "Call graph:\n" + "\n".join(lines) if lines else ""

    @staticmethod
    def _build_data_flow_excerpt(
        finding:  NormalizedFinding,
        dfg_deps: List[DataDependency],
        budget:   int,
    ) -> str:
        """Extract DFG dependency chains for variables mentioned in the finding."""
        tokens = set(re.findall(r'\b[a-z_][a-zA-Z0-9_]*\b', finding.line_text))
        relevant = [
            d for d in dfg_deps
            if d.source_var in tokens or d.target_var in tokens
        ]
        lines: List[str] = []
        total = 0
        for dep in relevant[:10]:
            line = f"  {dep.source_var} → {dep.target_var} (L{dep.line_number})"
            if total + len(line) + 2 > budget:
                break
            lines.append(line)
            total += len(line) + 2

        return "Data flow:\n" + "\n".join(lines) if lines else ""

    @staticmethod
    def _extract_cfg_nodes(cfg_graph: CFGGraph, budget: int) -> List[str]:
        """Return statement lists from CFG basic blocks, up to budget."""
        result: List[str] = []
        total  = 0
        for block in cfg_graph.blocks.values():
            if block.block_id in ("B_ENTRY", "B_EXIT"):
                continue
            for stmt in block.statements:
                if total + len(stmt) + 2 > budget:
                    return result
                result.append(stmt)
                total += len(stmt) + 2
        return result

    @staticmethod
    def _extract_rag_documents(evidence: EvidenceGraph, budget: int) -> List[str]:
        """Extract documentation / retrieved-context nodes from the evidence graph."""
        result: List[str] = []
        total  = 0
        for node in evidence.nodes.values():
            if node.node_type not in (
                EvidenceNodeType.DOCUMENTATION,
                EvidenceNodeType.RETRIEVED_CTX,
            ):
                continue
            text = node.metadata.get("text", node.label)
            if not text:
                continue
            if total + len(text) + 2 > budget:
                break
            result.append(text)
            total += len(text) + 2
        return result

    @staticmethod
    def _estimate_chars(window: ContextWindow) -> int:
        total  = len(window.target_file_content)
        total += sum(len(i) for i in window.required_includes)
        total += sum(len(f) for f in window.neighboring_functions)
        total += sum(len(c) for c in window.referenced_classes)
        total += sum(len(h) for h in window.inheritance_hierarchy)
        total += len(window.call_graph_excerpt)
        total += len(window.data_flow_excerpt)
        total += sum(len(s) for s in window.cfg_nodes)
        total += sum(len(d) for d in window.rag_documents)
        return total
