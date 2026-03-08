"""Tool base class and registry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Tool(ABC):
    """Abstract base class for agent tools."""

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        ...

    @property
    @abstractmethod
    def parameters(self) -> dict[str, Any]:
        ...

    @abstractmethod
    async def execute(self, **kwargs: Any) -> str:
        ...

    def to_schema(self) -> dict[str, Any]:
        """Convert to OpenAI function-calling schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """Dynamic registry for agent tools."""

    _HINT = "\n\n[Analyze the error above and try a different approach.]"

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def get_definitions(self) -> list[dict[str, Any]]:
        """Return all tool schemas in OpenAI format."""
        return [t.to_schema() for t in self._tools.values()]

    async def execute(self, name: str, params: dict[str, Any]) -> str:
        """Look up a tool by *name*, run it, and return the string result."""
        tool = self._tools.get(name)
        if not tool:
            avail = ", ".join(self._tools)
            return f"Error: Tool '{name}' not found. Available: {avail}"
        try:
            result = await tool.execute(**params)
            if isinstance(result, str) and result.startswith("Error"):
                return result + self._HINT
            return result
        except Exception as exc:
            return f"Error executing {name}: {exc}" + self._HINT

    @property
    def names(self) -> list[str]:
        return list(self._tools)
