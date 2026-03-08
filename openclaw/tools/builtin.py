"""Built-in tools: filesystem, shell, web, cron, spawn."""

from __future__ import annotations

import asyncio
import difflib
import html as html_mod
import json
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import httpx

from openclaw.tools.base import Tool

if TYPE_CHECKING:
    from openclaw.cron import CronService
    from openclaw.subagent import SubagentManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve(path: str, workspace: Path) -> Path:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = workspace / p
    return p.resolve()


# ===================================================================
# Filesystem tools
# ===================================================================

class ReadFileTool(Tool):
    name = "read_file"
    description = "Read the contents of a file."
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "File path to read"}},
        "required": ["path"],
    }
    _MAX = 128_000

    def __init__(self, workspace: Path):
        self._ws = workspace

    async def execute(self, path: str, **kw: Any) -> str:
        try:
            fp = _resolve(path, self._ws)
            if not fp.exists():
                return f"Error: File not found: {path}"
            if not fp.is_file():
                return f"Error: Not a file: {path}"
            text = fp.read_text(encoding="utf-8")
            if len(text) > self._MAX:
                return text[: self._MAX] + f"\n... (truncated, {len(text)} chars total)"
            return text
        except Exception as exc:
            return f"Error reading file: {exc}"


class WriteFileTool(Tool):
    name = "write_file"
    description = "Write content to a file. Creates parent directories if needed."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path"},
            "content": {"type": "string", "description": "Content to write"},
        },
        "required": ["path", "content"],
    }

    def __init__(self, workspace: Path):
        self._ws = workspace

    async def execute(self, path: str, content: str, **kw: Any) -> str:
        try:
            fp = _resolve(path, self._ws)
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(content, encoding="utf-8")
            return f"Wrote {len(content)} bytes to {fp}"
        except Exception as exc:
            return f"Error writing file: {exc}"


class EditFileTool(Tool):
    name = "edit_file"
    description = "Edit a file by replacing old_text with new_text (must match exactly)."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path"},
            "old_text": {"type": "string", "description": "Exact text to find"},
            "new_text": {"type": "string", "description": "Replacement text"},
        },
        "required": ["path", "old_text", "new_text"],
    }

    def __init__(self, workspace: Path):
        self._ws = workspace

    async def execute(self, path: str, old_text: str, new_text: str, **kw: Any) -> str:
        try:
            fp = _resolve(path, self._ws)
            if not fp.exists():
                return f"Error: File not found: {path}"
            content = fp.read_text(encoding="utf-8")
            if old_text not in content:
                return self._hint(old_text, content, path)
            if content.count(old_text) > 1:
                return f"Error: old_text appears {content.count(old_text)} times; provide more context."
            fp.write_text(content.replace(old_text, new_text, 1), encoding="utf-8")
            return f"Edited {fp}"
        except Exception as exc:
            return f"Error editing file: {exc}"

    @staticmethod
    def _hint(old: str, content: str, path: str) -> str:
        lines, old_lines = content.splitlines(True), old.splitlines(True)
        best_ratio, best_i = 0.0, 0
        for i in range(max(1, len(lines) - len(old_lines) + 1)):
            r = difflib.SequenceMatcher(None, old_lines, lines[i : i + len(old_lines)]).ratio()
            if r > best_ratio:
                best_ratio, best_i = r, i
        if best_ratio > 0.5:
            diff = "\n".join(difflib.unified_diff(
                old_lines, lines[best_i : best_i + len(old_lines)],
                fromfile="old_text", tofile=f"{path} (line {best_i + 1})", lineterm="",
            ))
            return f"Error: old_text not found in {path}.\nBest match ({best_ratio:.0%}) at line {best_i + 1}:\n{diff}"
        return f"Error: old_text not found in {path}. No similar text found."


class ListDirTool(Tool):
    name = "list_dir"
    description = "List directory contents."
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Directory path"}},
        "required": ["path"],
    }

    def __init__(self, workspace: Path):
        self._ws = workspace

    async def execute(self, path: str, **kw: Any) -> str:
        try:
            dp = _resolve(path, self._ws)
            if not dp.is_dir():
                return f"Error: Not a directory: {path}"
            items = sorted(dp.iterdir())
            if not items:
                return f"Directory {path} is empty"
            return "\n".join(
                f"{'📁' if i.is_dir() else '📄'} {i.name}" for i in items
            )
        except Exception as exc:
            return f"Error listing directory: {exc}"


# ===================================================================
# Shell
# ===================================================================

_DENY_PATTERNS = [
    r"\brm\s+-[rf]{1,2}\b",
    r"\bdel\s+/[fq]\b",
    r"\brmdir\s+/s\b",
    r"(?:^|[;&|]\s*)format\b",
    r"\b(mkfs|diskpart)\b",
    r"\bdd\s+if=",
    r">\s*/dev/sd",
    r"\b(shutdown|reboot|poweroff)\b",
    r":\(\)\s*\{.*\};\s*:",
]


class ShellTool(Tool):
    name = "exec"
    description = "Execute a shell command and return its output."
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to execute"},
        },
        "required": ["command"],
    }

    def __init__(self, workspace: Path, timeout: int = 60):
        self._ws = workspace
        self._timeout = timeout

    async def execute(self, command: str, **kw: Any) -> str:
        lower = command.strip().lower()
        for pat in _DENY_PATTERNS:
            if re.search(pat, lower):
                return "Error: Command blocked by safety guard."
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self._ws),
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), self._timeout)
            except asyncio.TimeoutError:
                proc.kill()
                return f"Error: Command timed out after {self._timeout}s"
            parts: list[str] = []
            if stdout:
                parts.append(stdout.decode("utf-8", errors="replace"))
            if stderr and stderr.strip():
                parts.append(f"STDERR:\n{stderr.decode('utf-8', errors='replace')}")
            if proc.returncode:
                parts.append(f"\nExit code: {proc.returncode}")
            result = "\n".join(parts) or "(no output)"
            if len(result) > 10_000:
                result = result[:10_000] + "\n... (truncated)"
            return result
        except Exception as exc:
            return f"Error executing command: {exc}"


# ===================================================================
# Web
# ===================================================================

_UA = "Mozilla/5.0 (compatible; OpenClaw/0.1)"


class WebSearchTool(Tool):
    name = "web_search"
    description = "Search the web via Brave Search API. Returns titles, URLs, snippets."
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "count": {"type": "integer", "description": "Number of results (1-10)"},
        },
        "required": ["query"],
    }

    def __init__(self, api_key: str | None = None):
        self._key = api_key

    async def execute(self, query: str, count: int = 5, **kw: Any) -> str:
        key = self._key or os.environ.get("BRAVE_API_KEY", "")
        if not key:
            return "Error: Brave Search API key not configured."
        count = max(1, min(count, 10))
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    params={"q": query, "count": count},
                    headers={"Accept": "application/json", "X-Subscription-Token": key},
                )
                r.raise_for_status()
            results = r.json().get("web", {}).get("results", [])[:count]
            if not results:
                return f"No results for: {query}"
            lines = [f"Results for: {query}\n"]
            for i, item in enumerate(results, 1):
                lines.append(f"{i}. {item.get('title', '')}\n   {item.get('url', '')}")
                if desc := item.get("description"):
                    lines.append(f"   {desc}")
            return "\n".join(lines)
        except Exception as exc:
            return f"Error: {exc}"


class WebFetchTool(Tool):
    name = "web_fetch"
    description = "Fetch a URL and extract readable text content."
    parameters = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to fetch"},
        },
        "required": ["url"],
    }
    _MAX = 50_000

    async def execute(self, url: str, **kw: Any) -> str:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return f"Error: Only http/https allowed, got '{parsed.scheme}'"
        try:
            from readability import Document

            async with httpx.AsyncClient(follow_redirects=True, timeout=30) as c:
                r = await c.get(url, headers={"User-Agent": _UA})
                r.raise_for_status()
            ctype = r.headers.get("content-type", "")
            if "application/json" in ctype:
                text = json.dumps(r.json(), indent=2, ensure_ascii=False)
            elif "text/html" in ctype or r.text[:256].lower().startswith(("<!doctype", "<html")):
                doc = Document(r.text)
                raw_html = doc.summary()
                text = f"# {doc.title()}\n\n{self._strip(raw_html)}" if doc.title() else self._strip(raw_html)
            else:
                text = r.text
            if len(text) > self._MAX:
                text = text[: self._MAX] + "\n... (truncated)"
            return text
        except Exception as exc:
            return f"Error fetching {url}: {exc}"

    @staticmethod
    def _strip(html: str) -> str:
        """Strip HTML tags and collapse whitespace."""
        text = re.sub(r"<script[\s\S]*?</script>", "", html, flags=re.I)
        text = re.sub(r"<style[\s\S]*?</style>", "", text, flags=re.I)
        text = re.sub(r"<[^>]+>", "", text)
        text = html_mod.unescape(text)
        return re.sub(r"\n{3,}", "\n\n", text).strip()


# ===================================================================
# Cron tool (interface to CronService)
# ===================================================================

class CronTool(Tool):
    name = "cron"
    description = "Schedule reminders and recurring tasks. Actions: add, list, remove."
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "list", "remove"],
                "description": "Action to perform",
            },
            "message": {"type": "string", "description": "Reminder message (for add)"},
            "every_seconds": {"type": "integer", "description": "Interval in seconds (recurring)"},
            "cron_expr": {"type": "string", "description": "Cron expression, e.g. '0 9 * * *'"},
            "at": {"type": "string", "description": "ISO datetime for one-time, e.g. '2026-03-10T10:30:00'"},
            "job_id": {"type": "string", "description": "Job ID (for remove)"},
        },
        "required": ["action"],
    }

    def __init__(self, service: CronService):
        self._svc = service
        self._channel = "cli"
        self._chat_id = "direct"

    def set_context(self, channel: str, chat_id: str) -> None:
        self._channel = channel
        self._chat_id = chat_id

    async def execute(self, action: str, **kw: Any) -> str:
        if action == "add":
            return self._add(**kw)
        if action == "list":
            return self._list()
        if action == "remove":
            return self._remove(kw.get("job_id"))
        return f"Unknown action: {action}"

    def _add(
        self,
        message: str = "",
        every_seconds: int | None = None,
        cron_expr: str | None = None,
        at: str | None = None,
        **_: Any,
    ) -> str:
        if not message:
            return "Error: message is required"
        from openclaw.cron import CronSchedule

        delete_after = False
        if every_seconds:
            sched = CronSchedule(kind="every", every_ms=every_seconds * 1000)
        elif cron_expr:
            sched = CronSchedule(kind="cron", expr=cron_expr)
        elif at:
            from datetime import datetime
            try:
                dt = datetime.fromisoformat(at)
            except ValueError:
                return f"Error: invalid datetime '{at}'"
            sched = CronSchedule(kind="at", at_ms=int(dt.timestamp() * 1000))
            delete_after = True
        else:
            return "Error: provide every_seconds, cron_expr, or at"
        job = self._svc.add_job(
            name=message[:30],
            schedule=sched,
            message=message,
            channel=self._channel,
            chat_id=self._chat_id,
            delete_after_run=delete_after,
        )
        return f"Created job '{job.name}' (id: {job.id})"

    def _list(self) -> str:
        jobs = self._svc.list_jobs()
        if not jobs:
            return "No scheduled jobs."
        return "Scheduled jobs:\n" + "\n".join(
            f"- {j.name} (id: {j.id}, {j.schedule.kind})" for j in jobs
        )

    def _remove(self, job_id: str | None) -> str:
        if not job_id:
            return "Error: job_id is required"
        return f"Removed job {job_id}" if self._svc.remove_job(job_id) else f"Job {job_id} not found"


# ===================================================================
# Spawn tool (interface to SubagentManager)
# ===================================================================

class SpawnTool(Tool):
    name = "spawn"
    description = (
        "Spawn a background sub-agent for a complex or time-consuming task. "
        "The sub-agent will complete the task independently and report back."
    )
    parameters = {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "Task description for the sub-agent"},
            "label": {"type": "string", "description": "Short label (for display)"},
        },
        "required": ["task"],
    }

    def __init__(self, manager: SubagentManager):
        self._mgr = manager
        self._channel = "cli"
        self._chat_id = "direct"

    def set_context(self, channel: str, chat_id: str) -> None:
        self._channel = channel
        self._chat_id = chat_id

    async def execute(self, task: str, label: str | None = None, **kw: Any) -> str:
        return await self._mgr.spawn(
            task=task,
            label=label,
            origin_channel=self._channel,
            origin_chat_id=self._chat_id,
        )
