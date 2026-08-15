import logging
from typing import Dict, List, Optional
from pydantic import BaseModel

logger = logging.getLogger("backend.analysis.symbols")


class ScopedSymbol(BaseModel):
    """Represent an individual registered symbol mapping inside a specific scope."""
    name: str
    symbol_type: str
    data_type: str = "Unknown"
    declared_line: int


class ScopedContext:
    """Class representing variable namespaces scopes and resolution maps."""

    def __init__(self, parent: Optional["ScopedContext"] = None):
        self.parent = parent
        self.symbols: Dict[str, ScopedSymbol] = {}

    def declare(self, symbol: ScopedSymbol):
        self.symbols[symbol.name] = symbol
        logger.debug(f"Declared symbol '{symbol.name}' as '{symbol.data_type}' in current scope.")

    def lookup(self, name: str) -> Optional[ScopedSymbol]:
        # Walk up namespaces scopes to resolve variables (shadow protection)
        if name in self.symbols:
            return self.symbols[name]
        if self.parent:
            return self.parent.lookup(name)
        return None

    def list_symbols(self) -> List[ScopedSymbol]:
        return list(self.symbols.values())
