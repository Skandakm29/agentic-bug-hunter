from typing import Any, Callable, Dict


class MCPToolRegistry:
    """Registry managing active MCP tool capabilities, execution wrappers, and timeout defaults."""

    _tools: Dict[str, Dict[str, Any]] = {}

    @classmethod
    def register(
        cls,
        name: str,
        func: Callable,
        timeout: float = 30.0,
        version: str = "1.0.0",
        cost: str = "low"
    ):
        cls._tools[name] = {
            "name": name,
            "func": func,
            "timeout": timeout,
            "version": version,
            "cost": cost
        }

    @classmethod
    def get_tool(cls, name: str) -> Dict[str, Any]:
        tool = cls._tools.get(name)
        if not tool:
            raise ValueError(f"MCP tool not registered: {name}")
        return tool

    @classmethod
    def list_tools(cls) -> Dict[str, Dict[str, Any]]:
        return cls._tools
