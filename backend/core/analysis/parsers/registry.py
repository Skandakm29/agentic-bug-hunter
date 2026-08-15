from typing import Dict, Type
from backend.core.analysis.parsers.base import BaseParser
from backend.core.analysis.parsers.cpp import CppParser


class ParserRegistry:
    """Registry class matching file extensions to programming language parser adapters."""

    _parsers: Dict[str, Type[BaseParser]] = {
        "cpp": CppParser,
        "h": CppParser,
        "hpp": CppParser,
        "cc": CppParser
    }

    @classmethod
    def register(cls, extension: str, parser_class: Type[BaseParser]) -> None:
        """
        Map a file extension to a parser implementation.

        This is the documented extension point for adding a language:

            ParserRegistry.register("py", PythonParser)
        """
        cls._parsers[extension.lower().lstrip(".")] = parser_class

    @classmethod
    def supported_extensions(cls) -> list:
        """Return every registered file extension."""
        return sorted(cls._parsers)

    @classmethod
    def get_parser(cls, extension: str) -> BaseParser:
        clean_ext = extension.lower().lstrip(".")
        parser_class = cls._parsers.get(clean_ext, CppParser)  # Default fallback to CppParser
        return parser_class()
