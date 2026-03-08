"""Heartbeat service — periodic agent wake-up."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Awaitable, Callable

log = logging.getLogger(__name__)

_HEARTBEAT_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "heartbeat",
            "description": "Report heartbeat decision after reviewing tasks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["skip", "run"],
                        "description": "skip = nothing to do, run = has active tasks",
                    },
                    "tasks": {
                        "type": "string",
                        "description": "Summary of active tasks (required when action=run)",
                    },
                },
                "required": ["action"],
            },
        },
    }
]


class HeartbeatService:
    """Reads HEARTBEAT.md periodically; asks the LLM to decide skip/run."""

    def __init__(
        self,
        workspace: Path,
        provider: "toyclaw.provider.OpenAIProvider",  # noqa: F821
        on_execute: Callable[[str], Awaitable[str]] | None = None,
        on_notify: Callable[[str], Awaitable[None]] | None = None,
        interval_s: int = 1800,
        enabled: bool = True,
    ):
        self.workspace = workspace
        self.provider = provider
        self.on_execute = on_execute
        self.on_notify = on_notify
        self.interval_s = interval_s
        self.enabled = enabled
        self._running = False
        self._task: asyncio.Task[None] | None = None

    @property
    def _file(self) -> Path:
        return self.workspace / "HEARTBEAT.md"

    async def start(self) -> None:
        if not self.enabled:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        log.info("Heartbeat started (every %ds)", self.interval_s)

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    # ------------------------------------------------------------------

    async def _loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self.interval_s)
                if self._running:
                    await self._tick()
            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("Heartbeat error")

    async def _tick(self) -> None:
        if not self._file.exists():
            return
        content = self._file.read_text(encoding="utf-8").strip()
        if not content:
            return

        # Phase 1: decision via tool call
        resp = await self.provider.chat(
            messages=[
                {"role": "system", "content": "You are a heartbeat agent. Call the heartbeat tool to report your decision."},
                {"role": "user", "content": f"Review HEARTBEAT.md and decide:\n\n{content}"},
            ],
            tools=_HEARTBEAT_TOOL,
        )
        if not resp.has_tool_calls:
            return
        args = resp.tool_calls[0].arguments
        if args.get("action") != "run":
            return

        # Phase 2: execute
        tasks = args.get("tasks", content)
        if self.on_execute:
            result = await self.on_execute(tasks)
            if result and self.on_notify:
                await self.on_notify(result)
