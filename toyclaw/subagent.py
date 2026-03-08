"""Sub-agent manager — run tasks in background async tasks."""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

from toyclaw.provider import OpenAIProvider
from toyclaw.tools.base import ToolRegistry
from toyclaw.tools.builtin import (
    EditFileTool,
    ListDirTool,
    ReadFileTool,
    ShellTool,
    WebFetchTool,
    WebSearchTool,
    WriteFileTool,
)


class SubagentManager:
    """Spawns lightweight background sub-agents."""

    def __init__(
        self,
        provider: OpenAIProvider,
        workspace: Path,
        on_complete: Callable[[str], Awaitable[None]] | None = None,
        brave_api_key: str | None = None,
        max_iterations: int = 15,
    ):
        self.provider = provider
        self.workspace = workspace
        self.on_complete = on_complete
        self.brave_api_key = brave_api_key
        self.max_iterations = max_iterations
        self._tasks: dict[str, asyncio.Task[None]] = {}

    async def spawn(
        self,
        task: str,
        label: str | None = None,
        origin_channel: str = "cli",
        origin_chat_id: str = "direct",
    ) -> str:
        """Spawn a background sub-agent. Returns immediately."""
        task_id = uuid.uuid4().hex[:8]
        display = label or (task[:30] + "..." if len(task) > 30 else task)

        bg = asyncio.create_task(self._run(task_id, task, display))
        self._tasks[task_id] = bg
        bg.add_done_callback(lambda _t: self._tasks.pop(task_id, None))

        return f"Sub-agent [{display}] started (id: {task_id}). Will notify when done."

    # ------------------------------------------------------------------

    async def _run(self, task_id: str, task: str, label: str) -> None:
        """Execute a sub-agent loop and announce the result."""
        try:
            result = await self._agent_loop(task)
        except Exception as exc:
            result = f"Error: {exc}"

        announcement = f"🔔 Background task [{label}] finished:\n{result}"
        if self.on_complete:
            try:
                await self.on_complete(announcement)
            except Exception:
                pass  # best-effort delivery

    async def _agent_loop(self, task: str) -> str:
        """Minimal agent loop (no spawn, no cron — prevent recursion)."""
        tools = self._build_tools()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt()},
            {"role": "user", "content": task},
        ]

        for _ in range(self.max_iterations):
            resp = await self.provider.chat(messages=messages, tools=tools.get_definitions())

            if resp.has_tool_calls:
                tc_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                        },
                    }
                    for tc in resp.tool_calls
                ]
                messages.append({"role": "assistant", "content": resp.content, "tool_calls": tc_dicts})

                for tc in resp.tool_calls:
                    result = await tools.execute(tc.name, tc.arguments)
                    messages.append({"role": "tool", "tool_call_id": tc.id, "name": tc.name, "content": result})
            else:
                return resp.content or "Task completed (no output)."

        return "Sub-agent reached iteration limit."

    def _build_tools(self) -> ToolRegistry:
        """Build a restricted tool set — NO spawn, NO cron."""
        reg = ToolRegistry()
        ws = self.workspace
        for cls in (ReadFileTool, WriteFileTool, EditFileTool, ListDirTool):
            reg.register(cls(workspace=ws))
        reg.register(ShellTool(workspace=ws))
        reg.register(WebSearchTool(api_key=self.brave_api_key))
        reg.register(WebFetchTool())
        return reg

    def _system_prompt(self) -> str:
        return (
            "# Sub-agent\n\n"
            "You are a background sub-agent spawned to complete a specific task.\n"
            "Stay focused. Your final text response will be reported back.\n\n"
            f"## Workspace\n{self.workspace}"
        )
