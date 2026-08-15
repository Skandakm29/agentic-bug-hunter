"""
C/C++ Declaration Helpers
=========================
Shared primitives for writing rules that reason about *declarations* rather
than raw line text.

Why this exists
---------------
The original rules matched keywords with ``substring in line.text`` and found
dereferences with a bare ``\\*(\\w+)`` regex. Both are far too loose on real
code:

- ``int accumulate(const uint8_t* samples, int n)`` tripped the uint8
  accumulator rule because ``"acc"`` is a substring of ``accumulate``.
- ``int readSensor(int channel);`` tripped the sensor type rule because
  ``"sensor"`` is a substring of ``readSensor``.
- ``extern PyObject* GetBases(PyTypeObject *type);`` tripped the null
  dereference rule because ``*type`` looks like a dereference, when it is a
  pointer *declarator* in a parameter list.

On a 265-file C header corpus that last case alone produced 109 of 110 total
findings. These helpers give rules the two distinctions they were missing:
declaration vs. call, and declarator vs. dereference.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Sequence, Set

# Type qualifiers and storage-class specifiers that may precede a type.
_QUALIFIERS = {
    "const", "static", "volatile", "extern", "register", "mutable",
    "inline", "constexpr", "thread_local", "signed", "unsigned",
    "struct", "class", "enum", "union", "typename", "auto",
}

# Keywords that can legally precede a unary '*' (so it is a dereference).
_DEREF_PRECEDING_KEYWORDS = {
    "return", "if", "while", "for", "switch", "case", "do", "else",
    "and", "or", "not", "sizeof", "delete", "throw", "co_return",
}

# Control-flow keywords that are never a declared type.
_NOT_A_TYPE = _DEREF_PRECEDING_KEYWORDS | {
    "true", "false", "nullptr", "NULL", "new", "operator", "template",
    "namespace", "using", "public", "private", "protected", "friend",
    "typedef", "goto", "break", "continue", "try", "catch",
}

_IDENT = r"[A-Za-z_]\w*"

# A block-comment continuation line: '*' followed by whitespace, end of line,
# or '/'. Deliberately does NOT match '*p = 5;', which is a dereference.
_COMMENT_CONTINUATION_RE = re.compile(r"^\*(?:\s|/|$)")


def _is_non_code(stripped: str) -> bool:
    """True for blank, preprocessor, comment, and block-comment lines."""
    if not stripped:
        return True
    if stripped.startswith(("#", "//", "/*")):
        return True
    return bool(_COMMENT_CONTINUATION_RE.match(stripped))


def strip_comments(line: str) -> str:
    """
    Remove trailing ``//`` and inline ``/* … */`` comments from *line*.

    A statement does not stop being a declaration because someone appended a
    note to it: ``uint8_t total = 0; // reviewed`` is still a uint8
    accumulator. Callers test for a trailing ``;``, so an unstripped comment
    silently hid the declaration.

    String and character literals are tracked so a ``//`` inside one -- as in
    ``const char* url = "http://example.com";`` -- is left intact.
    """
    out: List[str] = []
    in_string = in_char = False
    index = 0
    length = len(line)

    while index < length:
        ch = line[index]

        if in_string or in_char:
            out.append(ch)
            if ch == "\\" and index + 1 < length:      # escape sequence
                out.append(line[index + 1])
                index += 2
                continue
            if (in_string and ch == '"') or (in_char and ch == "'"):
                in_string = in_char = False
            index += 1
            continue

        if ch == '"':
            in_string = True
            out.append(ch)
        elif ch == "'":
            in_char = True
            out.append(ch)
        elif ch == "/" and index + 1 < length and line[index + 1] == "/":
            break                                      # rest of line is a comment
        elif ch == "/" and index + 1 < length and line[index + 1] == "*":
            end = line.find("*/", index + 2)
            if end == -1:
                break                                  # unterminated: drop the tail
            out.append(" ")
            index = end + 2
            continue
        else:
            out.append(ch)
        index += 1

    return "".join(out).strip()

# Tokens a declaration's left-hand side may contain. Anything else (an
# operator, a literal, punctuation) means it is not a declaration.
_DECL_LHS_TOKEN_RE = re.compile(
    rf"\s*(?:{_IDENT}|::|[\*&]|<[^<>]*>|\[[^\]]*\])"
)

# Any line that declares or defines a function: a name followed by a
# parameter list, then either ';' (prototype) or '{' (definition).
_FUNC_DECL_RE = re.compile(
    rf"{_IDENT}\s*\([^;]*\)\s*"
    r"(?:const\s*)?(?:noexcept\s*)?(?:override\s*)?(?:final\s*)?"
    r"(?:->[^;{]*)?\s*[;{]"
)


@dataclass(frozen=True)
class VarDecl:
    """A parsed variable declaration statement."""
    name:            str
    type_text:       str
    pointer_depth:   int
    has_initializer: bool
    is_array:        bool


# ---------------------------------------------------------------------------
# Declaration classification
# ---------------------------------------------------------------------------


def is_function_declaration(line: str) -> bool:
    """
    True when *line* declares or defines a function.

    Used to keep variable-oriented rules from firing on prototypes, where the
    identifier being matched is a function name, not a variable.
    """
    stripped = line.strip()
    if _is_non_code(stripped):
        return False
    stripped = strip_comments(stripped)
    return bool(stripped) and bool(_FUNC_DECL_RE.search(stripped))


def parse_variable_declaration(line: str) -> Optional[VarDecl]:
    """
    Parse *line* as a single variable declaration statement.

    Returns None when the line is not a declaration — including function
    prototypes, calls, control flow, preprocessor lines, and comments.
    """
    stripped = line.strip()
    if _is_non_code(stripped):
        return None
    stripped = strip_comments(stripped)

    # Cheap rejections first. These run in O(1)/O(n) and eliminate the vast
    # majority of lines -- including prose inside block comments -- before any
    # structural work happens.
    if not stripped.endswith(";"):
        return None
    body = stripped[:-1].strip()
    if not body:
        return None
    # A parameter list means this is a function, not a variable.
    if "(" in body or ")" in body or "{" in body or "}" in body:
        return None

    # Split off an initialiser at the first top-level '=' that is not part of
    # a comparison operator.
    lhs, has_initializer = body, False
    for i, ch in enumerate(body):
        if ch != "=":
            continue
        previous = body[i - 1] if i else ""
        following = body[i + 1] if i + 1 < len(body) else ""
        if previous in "!<>=+-*/%&|^" or following == "=":
            continue
        lhs, has_initializer = body[:i].strip(), True
        break
    if not lhs:
        return None

    # Tokenise the left-hand side linearly. Any character that is not part of
    # a declarator disqualifies the line, so this never backtracks.
    #
    # The previous implementation used one regex with a nested `(?:IDENT\s*)+?`
    # group. On a line of prose with no trailing ';' the engine explored every
    # split of the words before failing -- exponential time. A single comment
    # body line in a real header stalled a whole repository scan, which on a
    # service that ingests uploaded archives is a denial-of-service risk, not
    # just a slow path.
    tokens: List[str] = []
    pointer_depth = 0
    is_array = False
    position = 0
    while position < len(lhs):
        match = _DECL_LHS_TOKEN_RE.match(lhs, position)
        if not match:
            return None
        token = match.group(0).strip()
        position = match.end()
        if not token:
            continue
        if token == "*":
            pointer_depth += 1
        elif token.startswith("["):
            is_array = True
        elif token not in ("&", "::") and not token.startswith("<"):
            tokens.append(token)

    # Need at least a type and a name.
    if len(tokens) < 2:
        return None
    if any(t in _NOT_A_TYPE for t in tokens):
        return None

    name = tokens[-1]
    if name in _NOT_A_TYPE or name in _QUALIFIERS:
        return None
    if not re.fullmatch(_IDENT, name):
        return None

    type_text = " ".join(tokens[:-1])
    if not type_text:
        return None

    return VarDecl(
        name            = name,
        type_text       = type_text,
        pointer_depth   = pointer_depth,
        has_initializer = has_initializer,
        is_array        = is_array,
    )


# ---------------------------------------------------------------------------
# Identifier matching
# ---------------------------------------------------------------------------


def identifier_components(name: str) -> List[str]:
    """
    Split an identifier into lowercase word components.

    ``sample_count`` -> ['sample', 'count']
    ``totalValue``   -> ['total', 'value']
    ``accumulate``   -> ['accumulate']        (note: NOT ['acc', ...])
    ``HTTPResponse`` -> ['http', 'response']
    """
    parts: List[str] = []
    for chunk in re.split(r"[_\W]+", name):
        if not chunk:
            continue
        # Split camelCase and acronym boundaries.
        parts.extend(
            re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+", chunk)
        )
    return [p.lower() for p in parts if p]


def name_matches(name: str, keywords: Sequence[str]) -> bool:
    """
    True when *name* contains any of *keywords* as a whole word component.

    This is the substring fix: ``accumulate`` no longer matches ``acc``, while
    ``sample_count`` and ``totalBytes`` still match ``count`` and ``total``.
    """
    components = set(identifier_components(name))
    return any(k.lower() in components for k in keywords)


# ---------------------------------------------------------------------------
# Dereference detection
# ---------------------------------------------------------------------------


def find_dereferences(line: str) -> Set[str]:
    """
    Return identifiers dereferenced via unary ``*`` on *line*.

    A ``*`` is a **declarator** (``PyObject *type``, ``void f(int *p)``) when
    the preceding token is a type-like identifier, ``)``, ``>``, or ``]``. It
    is a **dereference** (``*p = 5``, ``return *p``, ``x = *p``) at the start
    of an expression -- after ``=``, ``(``, ``,``, ``{``, ``;``, an operator,
    a control keyword, or the start of the line.

    Multiplication (``a * b``) reads as a declarator here and is therefore not
    reported, which is the safe direction: this helper exists to avoid false
    dereference claims.
    """
    stripped = line.strip()
    if _is_non_code(stripped):
        return set()
    stripped = strip_comments(stripped)

    found: Set[str] = set()
    for match in re.finditer(rf"\*\s*({_IDENT})", stripped):
        prefix = stripped[: match.start()].rstrip()

        if not prefix:
            found.add(match.group(1))           # '*p = 5;' at line start
            continue

        last = prefix[-1]
        if last in "=(,{};[?:+-*/%<>!&|^~":
            found.add(match.group(1))
            continue

        # Preceding token is a word: a control keyword means dereference,
        # anything else is a type name and therefore a declarator.
        word = re.search(rf"({_IDENT})$", prefix)
        if word and word.group(1) in _DEREF_PRECEDING_KEYWORDS:
            found.add(match.group(1))

    return found


def find_assigned_names(line: str) -> Set[str]:
    """
    Identifiers that appear as the target of an assignment on *line*.

    Recognises ``p = ...``, ``p += ...``, and declarations with an
    initialiser. Used to decide that a pointer is no longer unset.
    """
    names: Set[str] = set()
    stripped = strip_comments(line.strip())

    for match in re.finditer(
        rf"(?:^|[;{{}}(,])\s*\*?\s*({_IDENT})\s*(?:\[[^\]]*\])?\s*"
        r"(?:\+|-|\*|/|%|&|\||\^|<<|>>)?=(?!=)",
        stripped,
    ):
        names.add(match.group(1))

    decl = parse_variable_declaration(stripped)
    if decl and decl.has_initializer:
        names.add(decl.name)

    return names
