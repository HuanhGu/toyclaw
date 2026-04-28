"""Agent loop — the core processing engine."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

from toyclaw.context import ContextBuilder
from toyclaw.memory import MemoryManager
from toyclaw.provider import LLMResponse, OpenAIProvider
from toyclaw.session import Session, SessionManager
from toyclaw.skills import SkillsLoader
from toyclaw.tools.base import ToolRegistry

_TOOL_RESULT_MAX = 500  # chars to keep per tool result in session history


class Agent:
    """The agent loop: receive message → build context → LLM ↔ tools → respond.

    This is the heart of ToyClaw.  Everything else feeds into or out of it.
    """

    def __init__(
        self,
        provider: OpenAIProvider,
        tools: ToolRegistry,
        workspace: Path,
        session_mgr: SessionManager | None = None,
        max_iterations: int = 30,
        memory_window: int = 100,
        on_output: Callable[[str], Awaitable[None]] | None = None,
    ):
        self.provider = provider
        self.tools = tools
        self.workspace = workspace
        self.sessions = session_mgr or SessionManager(workspace)
        self.max_iterations = max_iterations
        self.memory_window = memory_window
        self.on_output = on_output  # callback for async output (subagent results, cron)

        self._ctx = ContextBuilder(workspace)
        self._skills = SkillsLoader(workspace)
        self._memory = MemoryManager(workspace)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def process(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
    ) -> str:
        """Process a single user message and return the final reply."""
        # Set tool contexts
        self._set_tool_context(channel, chat_id)

        session = self.sessions.get_or_create(session_key)
        history = session.get_history(max_messages=self.memory_window)  #短期记忆
        memory_context = self._memory.format_search_context(content, limit=3)  # 注入历史记忆

        skills_summary = self._skills.build_summary()
        messages = self._ctx.build_messages(
            history=history,            # 短期会话上下文
            user_message=content,
            channel=channel,
            chat_id=chat_id,
            skills_summary=skills_summary,
            memory_context=memory_context,  # 长期记忆注入
        )

        final, _, all_msgs = await self._run_loop(messages)

        if final is None:
            final = "I've completed processing but have no response to give."

        self._save_turn(session, all_msgs, skip=1 + len(history))  # append: session保留了cli_direct.jsonl的所有内容 
        self.sessions.save(session) # ‘每轮’对话结束, 将对话保存到session
        return final

    # ------------------------------------------------------------------
    # Agent loop
    # ------------------------------------------------------------------

    async def _run_loop(
        self, messages: list[dict[str, Any]]
    ) -> tuple[str | None, list[str], list[dict[str, Any]]]:
        """Run the LLM ↔ tool iteration loop.

        Returns (final_content, tools_used, messages).
        """
        tools_used: list[str] = []
        final: str | None = None

        for _iter in range(self.max_iterations):
            response = await self.provider.chat(
                messages=messages,
                tools=self.tools.get_definitions(),
            )

            if response.has_tool_calls:
                # Append assistant message with tool calls
                tc_dicts = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                        },
                    }
                    for tc in response.tool_calls
                ]
                self._ctx.add_assistant_message(messages, response.content, tc_dicts)

                # Execute each tool
                for tc in response.tool_calls:
                    tools_used.append(tc.name)
                    result = await self.tools.execute(tc.name, tc.arguments)
                    self._ctx.add_tool_result(messages, tc.id, tc.name, result)
            else:
                # No tool calls → final response
                clean = self._strip_think(response.content)
                if response.finish_reason == "error":
                    final = clean or "Sorry, I encountered an error."
                    break
                self._ctx.add_assistant_message(messages, clean)
                final = clean
                break

        if final is None:
            final = (
                f"Reached the maximum number of iterations ({self.max_iterations}). "
                "Try breaking the task into smaller steps."
            )
        return final, tools_used, messages

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _set_tool_context(self, channel: str, chat_id: str) -> None:
        """Update routing context on tools that need it."""
        for name in ("cron", "spawn"):
            tool = self.tools.get(name)
            if tool and hasattr(tool, "set_context"):
                tool.set_context(channel, chat_id)

    def _save_turn(
        self, session: Session, messages: list[dict[str, Any]], skip: int
    ) -> None:
        """Persist new messages from this turn into the session."""
        now = datetime.now()
        for m in messages[skip:]:
            entry = dict(m)
            role = entry.get("role")
            content = entry.get("content")

            # Skip empty assistant messages (no content, no tool_calls)
            if role == "assistant" and not content and not entry.get("tool_calls"):
                continue

            # Strip runtime context tag from user messages
            if role == "user" and isinstance(content, str):
                entry["content"] = ContextBuilder.strip_runtime_tag(content)
                if not entry["content"]:
                    continue

            # Truncate oversized tool results
            if role == "tool" and isinstance(content, str) and len(content) > _TOOL_RESULT_MAX:
                entry["content"] = content[:_TOOL_RESULT_MAX] + "\n... (truncated)"

            entry.setdefault("timestamp", now.isoformat())
            session.messages.append(entry)
        session.updated_at = now

    @staticmethod
    def _strip_think(text: str | None) -> str | None:
        """Remove <think>…</think> blocks that some models embed."""
        if not text:
            return None
        return re.sub(r"<think>[\s\S]*?</think>", "", text).strip() or None
