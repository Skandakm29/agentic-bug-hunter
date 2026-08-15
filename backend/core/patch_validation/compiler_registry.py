from backend.core.patch_validation.compiler import GCCCompiler, ClangCompiler, MSVCCompiler, BaseCompiler


class CompilerRegistry:
    """Registry returning compiler instances for validation executions."""

    _compilers = {
        "gcc": GCCCompiler,
        "clang": ClangCompiler,
        "msvc": MSVCCompiler
    }

    @classmethod
    def register(cls, name: str, compiler_class) -> None:
        """
        Register a compiler backend.

            CompilerRegistry.register("intel", IntelCompiler)
        """
        cls._compilers[name.lower()] = compiler_class

    @classmethod
    def available(cls) -> list:
        """Return every registered compiler name."""
        return sorted(cls._compilers)

    @classmethod
    def get_compiler(cls, name: str) -> BaseCompiler:
        """
        Get compiler instance by name. Defaults to GCC compiler.

        Parameters
        ----------
        name : Compiler type (gcc, clang, msvc)
        """
        compiler_class = cls._compilers.get(name.lower(), GCCCompiler)
        return compiler_class()
