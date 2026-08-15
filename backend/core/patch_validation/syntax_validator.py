import re
from typing import List, Tuple


class SyntaxValidator:
    """Performs static syntax verification on patched files to reject corrupted files early."""

    def validate_code(self, code: str) -> Tuple[bool, List[str]]:
        """
        Validate C++ code blocks for brace, parenthesis, and bracket balance.

        Returns
        -------
        (success, errors) : Tuple[bool, List[str]]
        """
        errors = []

        # Remove string literals and comments to prevent false matching in comments/strings
        cleaned_code = self._clean_code(code)

        # 1. Balanced character verification
        stack = []
        mapping = {')': '(', '}': '{', ']': '['}
        line_numbers = self._get_line_numbers_mapping(code)

        for idx, char in enumerate(cleaned_code):
            if char in '({[':
                stack.append((char, idx))
            elif char in ')}]':
                expected = mapping[char]
                if not stack:
                    line_no = line_numbers[idx]
                    errors.append(f"Mismatched closing character '{char}' at line {line_no}")
                else:
                    top, _ = stack.pop()
                    if top != expected:
                        line_no = line_numbers[idx]
                        errors.append(
                            f"Mismatched closing character '{char}' (expected closing for '{top}') at line {line_no}"
                        )

        while stack:
            top, idx = stack.pop()
            line_no = line_numbers[idx]
            errors.append(f"Unclosed opening character '{top}' at line {line_no}")

        # 2. Check for basic preprocessor directives syntax
        errors.extend(self._validate_preprocessor(code))

        success = len(errors) == 0
        return success, errors

    # Comments and string/char literals, in one pass so a "//" inside a string
    # is not mistaken for a comment.
    _MASK_RE = re.compile(
        r'/\*.*?\*/'                 # block comment
        r'|//[^\n]*'                 # line comment
        r'|"(?:\\.|[^"\\\n])*"'      # double-quoted string
        r"|'(?:\\.|[^'\\\n])*'",     # single-quoted char
        re.DOTALL,
    )

    @staticmethod
    def _blank_out(match: re.Match) -> str:
        """
        Replace a matched region with an equal-length run of spaces, keeping
        newlines so both character offsets and line numbers stay aligned.
        """
        text = match.group(0)
        return "".join("\n" if ch == "\n" else " " for ch in text)

    def _clean_code(self, code: str) -> str:
        """
        Mask comments and literals *in place*.

        The result is the same length as the input, character for character.
        The previous implementation deleted the matched regions, so every
        index into the cleaned string pointed at a different character in the
        original -- and the reported line numbers, which are looked up against
        the original text, were wrong for any file containing a comment or a
        string literal.
        """
        return self._MASK_RE.sub(self._blank_out, code)

    def _get_line_numbers_mapping(self, code: str) -> List[int]:
        mapping = []
        current_line = 1
        for char in code:
            mapping.append(current_line)
            if char == '\n':
                current_line += 1
        return mapping

    def _validate_preprocessor(self, code: str) -> List[str]:
        errors = []
        lines = code.splitlines()
        for idx, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("#"):
                # Validate include syntax
                if stripped.startswith("#include"):
                    # Must be followed by <header.h> or "header.h"
                    match = re.match(r'^#include\s*(<[^>]+>|"[^"]+")$', stripped)
                    if not match:
                        errors.append(f"Malformed #include directive at line {idx + 1}: '{line}'")
                elif stripped.startswith("#define"):
                    # Check for simple define spacing
                    if not re.match(r'^#define\s+[a-zA-Z_][a-zA-Z0-9_]*', stripped):
                        errors.append(f"Malformed #define macro at line {idx + 1}: '{line}'")
        return errors
