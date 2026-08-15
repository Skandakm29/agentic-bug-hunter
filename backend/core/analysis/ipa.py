import re
import logging
from typing import Dict, List, Set
from pydantic import BaseModel, Field

logger = logging.getLogger("backend.analysis.ipa")


class CallEdge(BaseModel):
    """Represent an interprocedural call link between caller and callee."""
    caller: str
    callee: str
    line_number: int


class InterproceduralCallGraph(BaseModel):
    """Call graph trace registry mapping caller linkages."""
    edges: List[CallEdge] = Field(default_factory=list)


# Control-flow and declaration keywords that look like calls but are not.
_NON_CALL_KEYWORDS = {
    "if", "else", "while", "for", "switch", "case", "return", "sizeof",
    "catch", "throw", "new", "delete", "do", "and", "or", "not",
    "static_cast", "dynamic_cast", "const_cast", "reinterpret_cast",
}

# A function definition: optional return type / attributes / qualifiers, then
# the name, an argument list, optional trailing qualifiers, and an opening
# brace. Deliberately permissive on the prefix so that macro attributes such
# as `void IRAM_ATTR my_isr() {` are recognised.
_FUNC_DECL_RE = re.compile(
    r'^[\w:*&<>\s]+?\b(\w+)\s*\([^;]*\)\s*'
    r'(?:const\s*)?(?:noexcept\s*)?(?:override\s*)?(?:final\s*)?\{'
)


class IPATracer:
    """Traces calls across procedures and modules."""

    def trace_calls(self, code: str) -> InterproceduralCallGraph:
        graph = InterproceduralCallGraph()
        lines = code.split("\n")

        current_func = "global"
        # Brace depth relative to the enclosing function body. The previous
        # implementation reset scope on a bare "}" line, which broke on any
        # nested block and silently attributed calls to "global".
        func_depth = 0
        in_function = False

        for i, line in enumerate(lines):
            line_num = i + 1
            stripped = line.strip()
            if not stripped or stripped.startswith("//"):
                continue

            # Detect function declarations: e.g. void myFunc() {
            func_decl = _FUNC_DECL_RE.match(stripped)
            if func_decl and func_decl.group(1) not in _NON_CALL_KEYWORDS:
                current_func = func_decl.group(1)
                in_function  = True
                func_depth   = stripped.count("{") - stripped.count("}")
                continue

            # Detect function calls inside body: e.g. delay(10);
            for callee in re.findall(r'\b(\w+)\s*\(', stripped):
                if callee in _NON_CALL_KEYWORDS:
                    continue
                graph.edges.append(
                    CallEdge(
                        caller=current_func,
                        callee=callee,
                        line_number=line_num,
                    )
                )

            if in_function:
                func_depth += stripped.count("{") - stripped.count("}")
                if func_depth <= 0:
                    current_func = "global"
                    in_function  = False
                    func_depth   = 0

        return graph
