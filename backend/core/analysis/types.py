import re
import logging
from typing import Optional
from backend.core.analysis.symbols import ScopedContext

logger = logging.getLogger("backend.analysis.types")


class TypeInferencer:
    """Infers types of variables and assignments statically."""

    @classmethod
    def infer_expression_type(cls, expr: str, context: ScopedContext) -> str:
        clean_expr = expr.strip()

        # Constant Literals
        if re.match(r'^\d+$', clean_expr):
            return "int"
        if re.match(r'^["\'].*["\']$', clean_expr):
            return "std::string"
        if clean_expr in ["true", "false"]:
            return "bool"
        if re.match(r'^\d+\.\d+$', clean_expr):
            return "double"

        # Checked Variables Lookups
        symbol = context.lookup(clean_expr)
        if symbol:
            return symbol.data_type

        # Check call returns
        m = re.match(r'^(\w+)\(.*\)$', clean_expr)
        if m:
            func_name = m.group(1)
            # Default helper rules stubs
            if func_name.startswith("get"):
                return "int"
            if func_name.startswith("is"):
                return "bool"

        logger.debug(f"Type inference default to 'Unknown' for expression: '{expr}'")
        return "Unknown"
