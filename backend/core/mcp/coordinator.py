import asyncio
import time
import logging
from typing import Any, Dict
from backend.core.mcp.registry import MCPToolRegistry

logger = logging.getLogger("backend.mcp")


class MCPCoordinator:
    """Coordinator execution pipeline for running MCP tool capabilities under timeout constraints and logging."""

    @classmethod
    async def invoke_tool(cls, name: str, *args, **kwargs) -> Any:
        tool = MCPToolRegistry.get_tool(name)
        func = tool["func"]
        timeout = tool["timeout"]

        start_time = time.perf_counter()
        logger.info(f"Invoking MCP tool '{name}' (v{tool['version']}) with timeout={timeout}s...")

        try:
            # Execute tool call using wait_for wrapper to enforce timeouts
            if asyncio.iscoroutinefunction(func):
                result = await asyncio.wait_for(func(*args, **kwargs), timeout=timeout)
            else:
                # Wrap synchronous operations
                result = await asyncio.wait_for(
                    asyncio.to_thread(func, *args, **kwargs),
                    timeout=timeout
                )
            
            duration = time.perf_counter() - start_time
            logger.info(f"MCP tool '{name}' executed successfully in {duration:.4f}s.")
            return result

        except asyncio.TimeoutError:
            logger.error(f"Timeout occurred executing MCP tool '{name}' (limit: {timeout}s).")
            raise TimeoutError(f"Tool execution timed out after {timeout} seconds.")
        except Exception as e:
            logger.error(f"Error executing MCP tool '{name}': {e}")
            raise e
