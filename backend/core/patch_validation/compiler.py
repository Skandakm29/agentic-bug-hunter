import os
import time
import logging
import asyncio
import re
from abc import ABC, abstractmethod
from typing import List, Optional
from backend.core.patch_validation.validation_models import CompilationResult

logger = logging.getLogger("backend.patch_validation.compiler")


class BaseCompiler(ABC):
    """Abstract interface defining standard interactions with target compilers."""

    @abstractmethod
    async def compile(
        self,
        workspace_path: str,
        source_files: List[str],
        build_command: Optional[str] = None,
        timeout: int = 30,
    ) -> CompilationResult:
        """Execute compiler toolchain asynchronously."""
        pass

    def _parse_diagnostics(self, output: str, error_pattern: str, warning_pattern: str) -> tuple[List[str], List[str]]:
        errors = []
        warnings = []
        for line in output.splitlines():
            if re.search(error_pattern, line, re.IGNORECASE):
                errors.append(line.strip())
            elif re.search(warning_pattern, line, re.IGNORECASE):
                warnings.append(line.strip())
        return errors, warnings


class GCCCompiler(BaseCompiler):
    """Concrete runner for GCC/G++ compiler."""

    async def compile(
        self,
        workspace_path: str,
        source_files: List[str],
        build_command: Optional[str] = None,
        timeout: int = 30,
    ) -> CompilationResult:
        start_time = time.perf_counter()
        
        # If build command is provided, we delegate to that command
        if build_command:
            cmd = build_command
        else:
            # Default single file compilation invocation
            files_str = " ".join(source_files)
            cmd = f"g++ -Wall -Wextra -std=c++17 {files_str} -o val_out"

        try:
            logger.info(f"GCC compiling in {workspace_path} with command: {cmd}")
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workspace_path
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                stdout_str = stdout.decode("utf-8", errors="ignore")
                stderr_str = stderr.decode("utf-8", errors="ignore")
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return CompilationResult(
                    success=False,
                    compiler="gcc",
                    stderr="Compilation timed out.",
                    compile_time_ms=round((time.perf_counter() - start_time) * 1000, 2)
                )

            success = proc.returncode == 0
            errors, warnings = self._parse_diagnostics(
                stderr_str + stdout_str, 
                r"\berror\b|:\s*fatal error:", 
                r"\bwarning\b"
            )

            return CompilationResult(
                success=success,
                compiler="gcc",
                stdout=stdout_str,
                stderr=stderr_str,
                errors=errors,
                warnings=warnings,
                compile_time_ms=round((time.perf_counter() - start_time) * 1000, 2)
            )
        except Exception as e:
            logger.error(f"GCC Execution failed: {e}")
            return CompilationResult(
                success=False,
                compiler="gcc",
                stderr=str(e),
                errors=[f"Failed to execute GCC compiler: {e}"],
                compile_time_ms=round((time.perf_counter() - start_time) * 1000, 2)
            )


class ClangCompiler(BaseCompiler):
    """Concrete runner for Clang/Clang++ compiler."""

    async def compile(
        self,
        workspace_path: str,
        source_files: List[str],
        build_command: Optional[str] = None,
        timeout: int = 30,
    ) -> CompilationResult:
        start_time = time.perf_counter()
        
        if build_command:
            cmd = build_command
        else:
            files_str = " ".join(source_files)
            cmd = f"clang++ -Wall -Wextra -std=c++17 {files_str} -o val_out"

        try:
            logger.info(f"Clang compiling in {workspace_path} with command: {cmd}")
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workspace_path
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                stdout_str = stdout.decode("utf-8", errors="ignore")
                stderr_str = stderr.decode("utf-8", errors="ignore")
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return CompilationResult(
                    success=False,
                    compiler="clang",
                    stderr="Compilation timed out.",
                    compile_time_ms=round((time.perf_counter() - start_time) * 1000, 2)
                )

            success = proc.returncode == 0
            errors, warnings = self._parse_diagnostics(
                stderr_str + stdout_str, 
                r"\berror\b|:\s*fatal error:", 
                r"\bwarning\b"
            )

            return CompilationResult(
                success=success,
                compiler="clang",
                stdout=stdout_str,
                stderr=stderr_str,
                errors=errors,
                warnings=warnings,
                compile_time_ms=round((time.perf_counter() - start_time) * 1000, 2)
            )
        except Exception as e:
            logger.error(f"Clang Execution failed: {e}")
            return CompilationResult(
                success=False,
                compiler="clang",
                stderr=str(e),
                errors=[f"Failed to execute Clang compiler: {e}"],
                compile_time_ms=round((time.perf_counter() - start_time) * 1000, 2)
            )


class MSVCCompiler(BaseCompiler):
    """Concrete runner for MSVC (Microsoft Visual C++) compiler."""

    async def compile(
        self,
        workspace_path: str,
        source_files: List[str],
        build_command: Optional[str] = None,
        timeout: int = 30,
    ) -> CompilationResult:
        start_time = time.perf_counter()
        
        if build_command:
            cmd = build_command
        else:
            files_str = " ".join(source_files)
            cmd = f"cl.exe /EHsc /W4 {files_str}"

        try:
            logger.info(f"MSVC compiling in {workspace_path} with command: {cmd}")
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workspace_path
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                stdout_str = stdout.decode("utf-8", errors="ignore")
                stderr_str = stderr.decode("utf-8", errors="ignore")
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return CompilationResult(
                    success=False,
                    compiler="msvc",
                    stderr="Compilation timed out.",
                    compile_time_ms=round((time.perf_counter() - start_time) * 1000, 2)
                )

            success = proc.returncode == 0
            errors, warnings = self._parse_diagnostics(
                stderr_str + stdout_str, 
                r"\berror\s+C\d+:", 
                r"\bwarning\s+C\d+:"
            )

            return CompilationResult(
                success=success,
                compiler="msvc",
                stdout=stdout_str,
                stderr=stderr_str,
                errors=errors,
                warnings=warnings,
                compile_time_ms=round((time.perf_counter() - start_time) * 1000, 2)
            )
        except Exception as e:
            logger.error(f"MSVC Execution failed: {e}")
            return CompilationResult(
                success=False,
                compiler="msvc",
                stderr=str(e),
                errors=[f"Failed to execute MSVC compiler: {e}"],
                compile_time_ms=round((time.perf_counter() - start_time) * 1000, 2)
            )
