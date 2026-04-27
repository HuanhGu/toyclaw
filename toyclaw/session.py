"""Session management — conversation history with JSONL persistence."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from toyclaw.memory import MemoryManager


@dataclass
class Session:
    """A single conversation session."""

    key: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)

    def get_history(self, max_messages: int = 100) -> list[dict[str, Any]]:
        """Return recent messages, aligned to start on a user turn."""
        sliced = self.messages[-max_messages:]
        # Drop leading non-user messages to avoid orphaned tool results
        for i, m in enumerate(sliced):
            if m.get("role") == "user":
                sliced = sliced[i:]
                break
        out: list[dict[str, Any]] = []
        for m in sliced:
            entry: dict[str, Any] = {"role": m["role"], "content": m.get("content", "")}
            for k in ("tool_calls", "tool_call_id", "name"):
                if k in m:
                    entry[k] = m[k]
            out.append(entry)
        return out

    def clear(self) -> None:
        self.messages.clear()
        self.updated_at = datetime.now()


class SessionManager:
    """Manages sessions persisted as JSONL files."""

    def __init__(
        self,
        workspace: Path,
        memory_trigger_count: int = 20,
        memory_compact_batch_size: int = 10,
    ):
        self._dir = workspace / "sessions"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, Session] = {}
        self._memory = MemoryManager(
            workspace,
            trigger_count=memory_trigger_count,
            compact_batch_size=memory_compact_batch_size,
        )

    def get_or_create(self, key: str) -> Session:
        if key in self._cache:
            return self._cache[key]
        session = self._load(key) or Session(key=key)
        self._cache[key] = session
        return session

    def save(self, session: Session) -> None:
        path = self._path(session.key)
        with open(path, "w", encoding="utf-8") as f:
            meta = {
                "_type": "metadata",
                "key": session.key,
                "created_at": session.created_at.isoformat(),
                "updated_at": session.updated_at.isoformat(),
            }
            f.write(json.dumps(meta, ensure_ascii=False) + "\n")
            for msg in session.messages:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self._cache[session.key] = session  # 历史会话
        self._memory.maybe_compact(exclude_files={path.name})   # 记忆压缩相关

    # ------------------------------------------------------------------

    def _path(self, key: str) -> Path:
        safe = key.replace(":", "_").replace("/", "_")
        return self._dir / f"{safe}.jsonl"

    def _load(self, key: str) -> Session | None:
        path = self._path(key)
        if not path.exists():
            return None
        try:
            messages, created = [], None
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                data = json.loads(line)
                if data.get("_type") == "metadata":
                    created = datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None
                else:
                    messages.append(data)
            return Session(key=key, messages=messages, created_at=created or datetime.now())
        except Exception:
            return None
