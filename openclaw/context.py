"""Context builder — assembles system prompt and message lists."""

from __future__ import annotations

import platform
import time
from datetime import datetime
from pathlib import Path
from typing import Any

_BOOTSTRAP_FILES = ("AGENTS.md", "SOUL.md", "USER.md", "IDENTITY.md")
_RUNTIME_TAG = "[Runtime Context]"


class ContextBuilder:
    """Builds system prompt + message lists for the LLM."""

    def __init__(self, workspace: Path):
        self.workspace = workspace

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_messages(
        self,
        history: list[dict[str, Any]],
        user_message: str,
        channel: str | None = None,
        chat_id: str | None = None,
        skills_summary: str = "",
    ) -> list[dict[str, Any]]:
        """Return the full message list: [system, ...history, user]."""
        system = self._build_system_prompt(skills_summary)
        runtime = self._runtime_context(channel, chat_id)
        merged_user = f"{runtime}\n\n{user_message}"
        return [
            {"role": "system", "content": system},
            *history,
            {"role": "user", "content": merged_user},
        ]

    @staticmethod
    def add_assistant_message(
        messages: list[dict[str, Any]],
        content: str | None,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        msg: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        messages.append(msg)
        return messages

    @staticmethod
    def add_tool_result(
        messages: list[dict[str, Any]],
        tool_call_id: str,
        tool_name: str,
        result: str,
    ) -> list[dict[str, Any]]:
        messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "content": result,
        })
        return messages

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_system_prompt(self, skills_summary: str) -> str:
        ws = str(self.workspace.expanduser().resolve())
        system = platform.system()
        runtime = f"{'macOS' if system == 'Darwin' else system} {platform.machine()}"

        parts: list[str] = [
            f"# ToyClaw 🐾\n\n"
            f"You are ToyClaw, a helpful AI assistant.\n\n"
            f"## Runtime\n{runtime}, Python {platform.python_version()}\n\n"
            f"## Workspace\n"
            f"Your workspace is at: {ws}\n"
            f"- Long-term memory: {ws}/memory/MEMORY.md\n"
            f"- History log: {ws}/memory/HISTORY.md\n\n"
            f"## Guidelines\n"
            f"- State intent before tool calls, but NEVER predict results.\n"
            f"- Before modifying a file, read it first.\n"
            f"- If a tool call fails, analyze the error before retrying.\n"
            f"- Ask for clarification when the request is ambiguous.",
        ]

        # Load workspace bootstrap files
        for fname in _BOOTSTRAP_FILES:
            fp = self.workspace / fname
            if fp.exists():
                parts.append(f"## {fname}\n\n{fp.read_text(encoding='utf-8')}")

        # Memory context
        mem_file = self.workspace / "memory" / "MEMORY.md"
        if mem_file.exists():
            mem = mem_file.read_text(encoding="utf-8").strip()
            if mem:
                parts.append(f"## Long-term Memory\n\n{mem}")

        if skills_summary:
            parts.append(f"## Skills\n\n"
                         f"Read a skill's SKILL.md with read_file to use it.\n\n"
                         f"{skills_summary}")

        return "\n\n---\n\n".join(parts)

    @staticmethod
    def _runtime_context(channel: str | None, chat_id: str | None) -> str:
        now = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
        tz = time.strftime("%Z") or "UTC"
        lines = [_RUNTIME_TAG, f"Time: {now} ({tz})"]
        if channel and chat_id:
            lines.append(f"Channel: {channel}  Chat: {chat_id}")
        return "\n".join(lines)

    @staticmethod
    def strip_runtime_tag(content: str) -> str:
        """Strip the runtime context prefix from stored user messages."""
        if isinstance(content, str) and content.startswith(_RUNTIME_TAG):
            parts = content.split("\n\n", 1)
            return parts[1].strip() if len(parts) > 1 else ""
        return content
