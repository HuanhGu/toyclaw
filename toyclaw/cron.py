"""Cron service — lightweight scheduled-task engine backed by asyncio."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal


# ===================================================================
# Data types
# ===================================================================

@dataclass
class CronSchedule:
    kind: Literal["at", "every", "cron"]
    at_ms: int | None = None       # one-shot timestamp (ms)
    every_ms: int | None = None    # interval (ms)
    expr: str | None = None        # cron expression


@dataclass
class CronJob:
    id: str
    name: str
    schedule: CronSchedule
    message: str = ""
    channel: str = "cli"
    chat_id: str = "direct"
    enabled: bool = True
    delete_after_run: bool = False
    next_run_ms: int | None = None
    last_run_ms: int | None = None


# ===================================================================
# Helpers
# ===================================================================

def _now_ms() -> int:
    return int(time.time() * 1000)


def _next_run(sched: CronSchedule, now_ms: int) -> int | None:
    """Compute the next fire time in milliseconds."""
    if sched.kind == "at":
        return sched.at_ms if sched.at_ms and sched.at_ms > now_ms else None
    if sched.kind == "every" and sched.every_ms and sched.every_ms > 0:
        return now_ms + sched.every_ms
    if sched.kind == "cron" and sched.expr:
        try:
            from datetime import datetime

            from croniter import croniter

            base = datetime.fromtimestamp(now_ms / 1000).astimezone()
            return int(croniter(sched.expr, base).get_next(datetime).timestamp() * 1000)
        except Exception:
            return None
    return None


# ===================================================================
# Service
# ===================================================================

class CronService:
    """Persists jobs to a JSON file; fires them via an asyncio timer."""

    def __init__(
        self,
        store_path: Path,
        on_job: Callable[[CronJob], Awaitable[str | None]] | None = None,
    ):
        self._path = store_path
        self.on_job = on_job
        self._jobs: list[CronJob] = []
        self._running = False
        self._timer: asyncio.Task[None] | None = None

    # -- lifecycle -----------------------------------------------------

    async def start(self) -> None:
        self._load()
        self._recompute()
        self._save()
        self._running = True
        self._arm()

    def stop(self) -> None:
        self._running = False
        if self._timer:
            self._timer.cancel()
            self._timer = None

    # -- public API (called by CronTool) -------------------------------

    def list_jobs(self) -> list[CronJob]:
        self._load()
        return [j for j in self._jobs if j.enabled]

    def add_job(
        self,
        name: str,
        schedule: CronSchedule,
        message: str,
        channel: str = "cli",
        chat_id: str = "direct",
        delete_after_run: bool = False,
    ) -> CronJob:
        self._load()
        job = CronJob(
            id=uuid.uuid4().hex[:8],
            name=name,
            schedule=schedule,
            message=message,
            channel=channel,
            chat_id=chat_id,
            delete_after_run=delete_after_run,
            next_run_ms=_next_run(schedule, _now_ms()),
        )
        self._jobs.append(job)
        self._save()
        self._arm()
        return job

    def remove_job(self, job_id: str) -> bool:
        self._load()
        before = len(self._jobs)
        self._jobs = [j for j in self._jobs if j.id != job_id]
        if len(self._jobs) < before:
            self._save()
            self._arm()
            return True
        return False

    # -- internals -----------------------------------------------------

    def _load(self) -> None:
        if not self._path.exists():
            self._jobs = []
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            self._jobs = [
                CronJob(
                    id=j["id"],
                    name=j["name"],
                    schedule=CronSchedule(
                        kind=j["schedule"]["kind"],
                        at_ms=j["schedule"].get("at_ms"),
                        every_ms=j["schedule"].get("every_ms"),
                        expr=j["schedule"].get("expr"),
                    ),
                    message=j.get("message", ""),
                    channel=j.get("channel", "cli"),
                    chat_id=j.get("chat_id", "direct"),
                    enabled=j.get("enabled", True),
                    delete_after_run=j.get("delete_after_run", False),
                    next_run_ms=j.get("next_run_ms"),
                    last_run_ms=j.get("last_run_ms"),
                )
                for j in raw.get("jobs", [])
            ]
        except Exception:
            self._jobs = []

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "jobs": [
                {
                    "id": j.id,
                    "name": j.name,
                    "schedule": {"kind": j.schedule.kind, "at_ms": j.schedule.at_ms,
                                 "every_ms": j.schedule.every_ms, "expr": j.schedule.expr},
                    "message": j.message,
                    "channel": j.channel,
                    "chat_id": j.chat_id,
                    "enabled": j.enabled,
                    "delete_after_run": j.delete_after_run,
                    "next_run_ms": j.next_run_ms,
                    "last_run_ms": j.last_run_ms,
                }
                for j in self._jobs
            ]
        }
        self._path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def _recompute(self) -> None:
        now = _now_ms()
        for j in self._jobs:
            if j.enabled:
                j.next_run_ms = _next_run(j.schedule, now)

    def _arm(self) -> None:
        """Schedule the next timer tick."""
        if self._timer:
            self._timer.cancel()
        times = [j.next_run_ms for j in self._jobs if j.enabled and j.next_run_ms]
        if not times or not self._running:
            return
        delay_s = max(0, (min(times) - _now_ms())) / 1000

        async def _tick() -> None:
            await asyncio.sleep(delay_s)
            if self._running:
                await self._fire_due()

        self._timer = asyncio.create_task(_tick())

    async def _fire_due(self) -> None:
        self._load()
        now = _now_ms()
        due = [j for j in self._jobs if j.enabled and j.next_run_ms and now >= j.next_run_ms]
        for job in due:
            await self._execute(job)
        self._save()
        self._arm()

    async def _execute(self, job: CronJob) -> None:
        job.last_run_ms = _now_ms()
        if self.on_job:
            try:
                await self.on_job(job)
            except Exception:
                pass  # best-effort

        # Reschedule or disable
        if job.schedule.kind == "at":
            if job.delete_after_run:
                self._jobs = [j for j in self._jobs if j.id != job.id]
            else:
                job.enabled = False
                job.next_run_ms = None
        else:
            job.next_run_ms = _next_run(job.schedule, _now_ms())
