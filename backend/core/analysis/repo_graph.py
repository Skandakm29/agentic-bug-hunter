import logging
from typing import Dict, List, Set
from pydantic import BaseModel, Field

logger = logging.getLogger("backend.analysis.repo_graph")


class SymbolNode(BaseModel):
    """Represent an individual code symbol entity (class, function, variable)."""
    name: str
    symbol_type: str  # function, class, variable
    file_path: str
    line_number: int
    dependencies: List[str] = Field(default_factory=list)


class RepositoryKnowledgeGraph:
    """
    Graph structure mapping code structure hierarchies, import calls, and dependencies.

    Maintains reverse and by-file indexes alongside the primary maps. A
    repository-wide scan shares one graph across every file, so the naive
    implementations -- ``get_callers`` scanning every call-graph entry and
    callers filtering every symbol by ``file_path`` -- turned per-finding work
    into a full traversal of the accumulated graph. That made whole-repository
    scans quadratic in the number of files.

    ``version`` increments on every mutation so consumers can memoise derived
    results (cycle detection, for instance) and recompute only when the graph
    actually changes.
    """

    def __init__(self):
        self.symbols: Dict[str, SymbolNode] = {}
        # Mapping: caller_symbol_name -> Set[callee_symbol_name]
        self.call_graph: Dict[str, Set[str]] = {}
        # Mapping: file_path -> Set[import_path]
        self.dependencies: Dict[str, Set[str]] = {}

        # Derived indexes, kept in sync by the mutators below.
        self._callers: Dict[str, Set[str]] = {}       # callee -> Set[caller]
        self._by_file: Dict[str, Set[str]] = {}       # file_path -> Set[symbol name]
        self.version: int = 0

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def register_symbol(self, symbol: SymbolNode):
        previous = self.symbols.get(symbol.name)
        if previous is not None and previous.file_path != symbol.file_path:
            # Re-homed symbol: drop it from its old file bucket.
            bucket = self._by_file.get(previous.file_path)
            if bucket:
                bucket.discard(symbol.name)

        self.symbols[symbol.name] = symbol
        self._by_file.setdefault(symbol.file_path, set()).add(symbol.name)
        self.version += 1
        logger.debug("Registered repository symbol: %s '%s'", symbol.symbol_type, symbol.name)

    def add_call(self, caller: str, callee: str):
        callees = self.call_graph.setdefault(caller, set())
        if callee in callees:
            return                                    # already recorded
        callees.add(callee)
        self._callers.setdefault(callee, set()).add(caller)
        self.version += 1
        logger.debug("Call-graph edge added: %s() -> %s()", caller, callee)

    def add_dependency(self, file_path: str, dependency_path: str):
        deps = self.dependencies.setdefault(file_path, set())
        if dependency_path in deps:
            return
        deps.add(dependency_path)
        self.version += 1
        logger.debug("Dependency edge added: '%s' depends on '%s'", file_path, dependency_path)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_callers(self, callee: str) -> List[str]:
        """Every symbol that calls *callee*. O(1) via the reverse index."""
        return sorted(self._callers.get(callee, ()))

    def get_dependencies(self, file_path: str) -> List[str]:
        return list(self.dependencies.get(file_path, set()))

    def symbols_in_file(self, file_path: str) -> List[SymbolNode]:
        """Every symbol declared in *file_path*. O(k) in that file's symbols."""
        names = self._by_file.get(file_path)
        if not names:
            return []
        return [self.symbols[n] for n in names if n in self.symbols]

    def symbol_names_in_file(self, file_path: str) -> Set[str]:
        """Names only — avoids materialising SymbolNode objects."""
        return set(self._by_file.get(file_path, ()))
