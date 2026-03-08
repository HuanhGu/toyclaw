"""CLI entry-point — interactive REPL and one-shot mode."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from toyclaw.agent import Agent
from toyclaw.config import Config, load_config
from toyclaw.cron import CronService
from toyclaw.heartbeat import HeartbeatService
from toyclaw.provider import OpenAIProvider
from toyclaw.session import SessionManager
from toyclaw.skills import SkillsLoader
from toyclaw.subagent import SubagentManager
from toyclaw.tools.base import ToolRegistry
from toyclaw.tools.builtin import (
    CronTool,
    EditFileTool,
    ListDirTool,
    ReadFileTool,
    ShellTool,
    SpawnTool,
    WebFetchTool,
    WebSearchTool,
    WriteFileTool,
)

log = logging.getLogger("toyclaw")

_EXIT_CMDS = {"exit", "quit", "/exit", "/quit", ":q"}


# ===================================================================
# Bootstrap helpers
# ===================================================================

def _ensure_workspace(ws: Path) -> None:
    for sub in ("memory", "sessions", "skills", "cron"):
        (ws / sub).mkdir(parents=True, exist_ok=True)


def _build_stack(cfg: Config):
    """Wire up every component and return (agent, cron_service, heartbeat)."""
    ws = cfg.workspace
    _ensure_workspace(ws)

    provider = OpenAIProvider(api_key=cfg.api_key, api_base=cfg.api_base, default_model=cfg.model)
    session_mgr = SessionManager(ws)

    # Async output callback (for subagent results & cron)
    async def _print_async(text: str) -> None:
        print(f"\n{text}\n> ", end="", flush=True)

    # Cron
    cron_svc = CronService(store_path=ws / "cron" / "jobs.json")
    subagent_mgr = SubagentManager(
        provider=provider, workspace=ws,
        on_complete=_print_async, brave_api_key=cfg.brave_api_key,
    )

    # Tools
    tools = ToolRegistry()
    for cls in (ReadFileTool, WriteFileTool, EditFileTool, ListDirTool):
        tools.register(cls(workspace=ws))
    tools.register(ShellTool(workspace=ws))
    tools.register(WebSearchTool(api_key=cfg.brave_api_key))
    tools.register(WebFetchTool())
    tools.register(CronTool(service=cron_svc))
    tools.register(SpawnTool(manager=subagent_mgr))

    agent = Agent(
        provider=provider, tools=tools, workspace=ws,
        session_mgr=session_mgr, max_iterations=cfg.max_iterations,
        on_output=_print_async,
    )

    # Wire cron callback → agent.process
    async def _on_cron(job) -> str | None:
        result = await agent.process(
            content=job.message,
            session_key=f"cron:{job.id}",
            channel=job.channel,
            chat_id=job.chat_id,
        )
        if result:
            await _print_async(f"⏰ [{job.name}]: {result}")
        return result

    cron_svc.on_job = _on_cron

    # Heartbeat
    heartbeat = HeartbeatService(
        workspace=ws, provider=provider,
        on_execute=lambda tasks: agent.process(tasks, session_key="heartbeat:main"),
        on_notify=_print_async,
        interval_s=cfg.heartbeat_interval,
        enabled=cfg.heartbeat_enabled,
    )

    return agent, cron_svc, heartbeat


# ===================================================================
# Main
# ===================================================================

async def _async_main(cfg: Config, one_shot: str | None = None) -> None:
    agent, cron_svc, heartbeat = _build_stack(cfg)

    # Start background services
    await cron_svc.start()
    await heartbeat.start()

    try:
        if one_shot:
            resp = await agent.process(one_shot)
            print(resp)
            return

        print("🐾 ToyClaw ready. Type 'exit' to quit.\n")
        loop = asyncio.get_event_loop()
        while True:
            try:
                user_input = await loop.run_in_executor(None, lambda: input("> "))
            except (EOFError, KeyboardInterrupt):
                print()
                break
            text = user_input.strip()
            if not text:
                continue
            if text.lower() in _EXIT_CMDS:
                break
            if text == "/new":
                session = agent.sessions.get_or_create("cli:direct")
                session.clear()
                agent.sessions.save(session)
                print("New session started.\n")
                continue
            resp = await agent.process(text)
            print(f"\n{resp}\n")
    finally:
        cron_svc.stop()
        heartbeat.stop()


def main() -> None:
    """CLI entry-point."""
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    # Minimal arg parsing (no argparse dependency bloat)
    args = sys.argv[1:]
    config_path: Path | None = None
    one_shot: str | None = None

    i = 0
    while i < len(args):
        if args[i] in ("-c", "--config") and i + 1 < len(args):
            config_path = Path(args[i + 1])
            i += 2
        elif args[i] in ("-m", "--message") and i + 1 < len(args):
            one_shot = args[i + 1]
            i += 2
        else:
            i += 1

    cfg = load_config(config_path)
    if not cfg.api_key:
        print("Error: api_key not set. Configure ~/.toyclaw/config.json")
        sys.exit(1)

    asyncio.run(_async_main(cfg, one_shot))
