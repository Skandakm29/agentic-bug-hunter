import re
from typing import Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, Field


class DataDependency(BaseModel):
    """Represent data propagation link between two variables or constants."""
    source_var: str
    target_var: str
    line_number: int


class DFGGraph(BaseModel):
    """Represent the complete data dependency graph."""
    dependencies: List[DataDependency] = Field(default_factory=list)


# Identifiers that are never data-flow targets or sources
_KEYWORDS = {
    "if", "else", "for", "while", "do", "switch", "case", "default", "return",
    "break", "continue", "goto", "sizeof", "new", "delete", "throw", "try",
    "catch", "true", "false", "nullptr", "NULL", "const", "static", "inline",
    "volatile", "extern", "register", "constexpr", "auto", "struct", "class",
    "enum", "union", "typedef", "namespace", "using", "template", "typename",
    "public", "private", "protected", "virtual", "override", "operator",
}

# Compound-assignment operators, longest first so "<<=" wins over "<".
_COMPOUND_OPS = ("<<=", ">>=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=")


class DFGConstructor:
    """Traverses code strings to compile data flow dependencies."""

    def construct(self, lines: List[str]) -> DFGGraph:
        dfg = DFGGraph()

        # Track active definitions
        # Mapping: variable_name -> last_line_assigned
        active_defs: Dict[str, int] = {}

        for i, line in enumerate(lines):
            line_num = i + 1
            stripped = line.strip()
            if not stripped:
                continue

            parsed = self._parse_assignment(stripped)
            if not parsed:
                continue
            target_var, source_expr = parsed

            # Find source variables referenced in the assignment RHS
            for src in re.findall(r'\b([A-Za-z_]\w*)\b', source_expr):
                # Ignore numeric literals, keywords, and self-references
                if src in _KEYWORDS or src == target_var:
                    continue
                dfg.dependencies.append(
                    DataDependency(
                        source_var=src,
                        target_var=target_var,
                        line_number=line_num,
                    )
                )

            # Update definition line
            active_defs[target_var] = line_num

        return dfg

    @staticmethod
    def _parse_assignment(stripped: str) -> Optional[Tuple[str, str]]:
        """
        Return ``(target_var, rhs_expression)`` for an assignment statement,
        or None if the line is not an assignment.

        Handles plain assignment (``x = y;``), declarations with a type
        (``int x = y;``, ``std::vector<int> v = w;``, ``int *p = q;``), and
        compound assignment (``x += y;``).  The previous implementation
        required the target to be the very first token, so every typed
        declaration -- the majority of assignments in real C++ -- was missed.
        """
        if not stripped.endswith(";"):
            return None
        body = stripped[:-1]

        # Locate the assignment operator, skipping ==, !=, <=, >=.
        split_at = -1
        op_len = 1
        for op in _COMPOUND_OPS:
            idx = body.find(op)
            if idx != -1:
                split_at, op_len = idx, len(op)
                break
        if split_at == -1:
            for idx, ch in enumerate(body):
                if ch != "=":
                    continue
                prev = body[idx - 1] if idx > 0 else ""
                nxt  = body[idx + 1] if idx + 1 < len(body) else ""
                if prev in "!<>=" or nxt == "=":
                    continue
                split_at = idx
                break
        if split_at == -1:
            return None

        lhs = body[:split_at].strip()
        rhs = body[split_at + op_len:].strip()
        if not lhs or not rhs:
            return None

        # A '(' or '[' on the left means a call or subscript, not a simple
        # scalar definition we can attribute cleanly.
        if "(" in lhs or "[" in lhs:
            return None

        # The target is the last identifier on the left, which strips any
        # type prefix, pointer/reference sigils, and template arguments.
        identifiers = re.findall(r'[A-Za-z_]\w*', lhs)
        if not identifiers:
            return None
        target = identifiers[-1]
        if target in _KEYWORDS:
            return None

        return target, rhs
