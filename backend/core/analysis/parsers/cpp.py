import re
from backend.core.analysis.parsers.base import BaseParser, CodeLine, CodeRepresentation


class CppParser(BaseParser):
    """C++ parser mapping code lines to normalized intermediate representation (IR) schemas."""

    def parse(self, code: str) -> CodeRepresentation:
        lines = code.split("\n")
        parsed_lines = []
        
        in_isr = False
        brace_depth = 0
        in_multiline_comment = False

        for i, line in enumerate(lines):
            stripped = line.strip()
            line_num = i + 1

            # Stateful multiline comment check
            if stripped.startswith("/*"):
                in_multiline_comment = True
            
            is_comment = in_multiline_comment or stripped.startswith("//")
            
            if in_multiline_comment and "*/" in stripped:
                in_multiline_comment = False

            # Stateful ISR / Brace depth checks
            if re.search(r'\bISR\b|\bIRQ\b|interrupt|IRAM_ATTR|__irq', line, re.IGNORECASE):
                in_isr = True
                brace_depth = 0

            # Compute line brace depth changes
            brace_depth += line.count("{") - line.count("}")

            contains_rdi = "rdi." in line or stripped.startswith(".")

            parsed_lines.append(
                CodeLine(
                    line_number=line_num,
                    text=line,
                    stripped=stripped,
                    is_comment=is_comment,
                    in_isr=in_isr,
                    brace_depth=brace_depth,
                    contains_rdi=contains_rdi
                )
            )

            # De-register ISR context when the outermost block closes
            if in_isr and brace_depth <= 0 and "}" in line:
                in_isr = False

        return CodeRepresentation(
            lines=parsed_lines,
            raw_code=code,
            metadata={"language": "cpp", "total_lines": len(lines)}
        )
