import os
import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import Optional, List
from backend.core.patch_validation.validation_models import CompilationResult
from backend.core.patch_validation.compiler_registry import CompilerRegistry

logger = logging.getLogger("backend.patch_validation.build_system")


class BaseBuildSystem(ABC):
    """Abstract interface defining the build configuration and compile execution contracts."""

    @abstractmethod
    async def configure(self, workspace_path: str, compiler_type: str, parallel_jobs: int = 4) -> bool:
        """Run build system configuration step."""
        pass

    @abstractmethod
    async def build(
        self,
        workspace_path: str,
        compiler_type: str,
        parallel_jobs: int = 4,
        timeout: int = 60,
    ) -> CompilationResult:
        """Execute the compile phase."""
        pass


class CMakeBuildSystem(BaseBuildSystem):
    """CMake build pipeline executor."""

    async def configure(self, workspace_path: str, compiler_type: str, parallel_jobs: int = 4) -> bool:
        # Determine target compiler executables
        cxx_compiler = "g++"
        if compiler_type == "clang":
            cxx_compiler = "clang++"
        elif compiler_type == "msvc":
            cxx_compiler = "cl"

        cmd = f"cmake -S . -B build -DCMAKE_CXX_COMPILER={cxx_compiler}"
        try:
            logger.info(f"CMake configuring in {workspace_path} with cmd: {cmd}")
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workspace_path
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.warning(f"CMake configure failed: {stderr.decode(errors='ignore')}")
                return False
            return True
        except Exception as e:
            logger.error(f"CMake configure failed: {e}")
            return False

    async def build(
        self,
        workspace_path: str,
        compiler_type: str,
        parallel_jobs: int = 4,
        timeout: int = 60,
    ) -> CompilationResult:
        cmd = f"cmake --build build -j {parallel_jobs}"
        compiler = CompilerRegistry.get_compiler(compiler_type)
        # We delegate compilation execution to compiler class but specify CMake build command
        return await compiler.compile(
            workspace_path=workspace_path,
            source_files=[],
            build_command=cmd,
            timeout=timeout
        )


class MakeBuildSystem(BaseBuildSystem):
    """Make execution build pipeline."""

    async def configure(self, workspace_path: str, compiler_type: str, parallel_jobs: int = 4) -> bool:
        configure_script = os.path.join(workspace_path, "configure")
        if os.path.exists(configure_script):
            cmd = "./configure"
            try:
                logger.info(f"Make configuring with cmd: {cmd}")
                proc = await asyncio.create_subprocess_shell(
                    cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=workspace_path
                )
                await proc.communicate()
                return proc.returncode == 0
            except Exception as e:
                logger.error(f"Make configure script failed: {e}")
                return False
        return True

    async def build(
        self,
        workspace_path: str,
        compiler_type: str,
        parallel_jobs: int = 4,
        timeout: int = 60,
    ) -> CompilationResult:
        cmd = f"make -j {parallel_jobs}"
        compiler = CompilerRegistry.get_compiler(compiler_type)
        return await compiler.compile(
            workspace_path=workspace_path,
            source_files=[],
            build_command=cmd,
            timeout=timeout
        )


class NinjaBuildSystem(BaseBuildSystem):
    """Ninja build system executor."""

    async def configure(self, workspace_path: str, compiler_type: str, parallel_jobs: int = 4) -> bool:
        cxx_compiler = "g++"
        if compiler_type == "clang":
            cxx_compiler = "clang++"
        elif compiler_type == "msvc":
            cxx_compiler = "cl"

        cmd = f"cmake -G Ninja -S . -B build -DCMAKE_CXX_COMPILER={cxx_compiler}"
        try:
            logger.info(f"Ninja CMake configuring: {cmd}")
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workspace_path
            )
            await proc.communicate()
            return proc.returncode == 0
        except Exception as e:
            logger.error(f"Ninja configure failed: {e}")
            return False

    async def build(
        self,
        workspace_path: str,
        compiler_type: str,
        parallel_jobs: int = 4,
        timeout: int = 60,
    ) -> CompilationResult:
        cmd = f"ninja -C build -j {parallel_jobs}"
        compiler = CompilerRegistry.get_compiler(compiler_type)
        return await compiler.compile(
            workspace_path=workspace_path,
            source_files=[],
            build_command=cmd,
            timeout=timeout
        )


class BazelBuildSystem(BaseBuildSystem):
    """Bazel build pipeline executor."""

    async def configure(self, workspace_path: str, compiler_type: str, parallel_jobs: int = 4) -> bool:
        return True

    async def build(
        self,
        workspace_path: str,
        compiler_type: str,
        parallel_jobs: int = 4,
        timeout: int = 60,
    ) -> CompilationResult:
        cmd = f"bazel build //..."
        compiler = CompilerRegistry.get_compiler(compiler_type)
        return await compiler.compile(
            workspace_path=workspace_path,
            source_files=[],
            build_command=cmd,
            timeout=timeout
        )


class NoneBuildSystem(BaseBuildSystem):
    """Fallback build system compiling files directly using compiler compiler_type (suitable for single-file scenarios)."""

    async def configure(self, workspace_path: str, compiler_type: str, parallel_jobs: int = 4) -> bool:
        return True

    async def build(
        self,
        workspace_path: str,
        compiler_type: str,
        parallel_jobs: int = 4,
        timeout: int = 60,
    ) -> CompilationResult:
        # Find all .cpp / .cc / .cxx files to compile
        source_files = []
        for root, _, files in os.walk(workspace_path):
            # Ignore hidden dirs or build directories to avoid re-compiling artifacts
            if any(part.startswith('.') or part == "build" for part in root.split(os.sep)):
                continue
            for file in files:
                if file.endswith((".cpp", ".cc", ".cxx")):
                    source_files.append(os.path.relpath(os.path.join(root, file), workspace_path))

        compiler = CompilerRegistry.get_compiler(compiler_type)
        if not source_files:
            return CompilationResult(
                success=True,
                compiler=compiler_type,
                stdout="No source files found to build (direct mode).",
                compile_time_ms=0.0
            )

        return await compiler.compile(
            workspace_path=workspace_path,
            source_files=source_files,
            timeout=timeout
        )


class BuildSystemRegistry:
    """Registry returning build system executors by type."""

    _systems = {
        "cmake": CMakeBuildSystem,
        "make": MakeBuildSystem,
        "ninja": NinjaBuildSystem,
        "bazel": BazelBuildSystem,
        "none": NoneBuildSystem
    }

    @classmethod
    def register(cls, name: str, system_class) -> None:
        """
        Register a build system backend.

            BuildSystemRegistry.register("meson", MesonBuildSystem)
        """
        cls._systems[name.lower()] = system_class

    @classmethod
    def available(cls) -> list:
        """Return every registered build system name."""
        return sorted(cls._systems)

    @classmethod
    def get_build_system(cls, name: str) -> BaseBuildSystem:
        """Get build system class by name (defaults to NoneBuildSystem)."""
        system_class = cls._systems.get(name.lower(), NoneBuildSystem)
        return system_class()
