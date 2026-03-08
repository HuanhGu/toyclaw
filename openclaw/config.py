"""Configuration loader."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

_DEFAULT_DIR = Path.home() / ".toyclaw"


@dataclass
class Config:
    """Application configuration."""

    api_key: str = ""
    api_base: str = "https://api.openai.com/v1"
    model: str = "gpt-4o"
    workspace: Path = field(default_factory=lambda: _DEFAULT_DIR / "workspace")
    brave_api_key: str | None = None
    max_iterations: int = 30
    heartbeat_interval: int = 1800  # seconds
    heartbeat_enabled: bool = True


def load_config(path: Path | None = None) -> Config:
    """Load config from JSON file. Missing keys use defaults."""
    path = path or _DEFAULT_DIR / "config.json"
    if not path.exists():
        return Config()
    raw = json.loads(path.read_text(encoding="utf-8"))
    kwargs: dict = {}
    for key in ("api_key", "api_base", "model", "brave_api_key"):
        if key in raw:
            kwargs[key] = raw[key]
    for key in ("max_iterations", "heartbeat_interval"):
        if key in raw:
            kwargs[key] = int(raw[key])
    if "heartbeat_enabled" in raw:
        kwargs["heartbeat_enabled"] = bool(raw["heartbeat_enabled"])
    if "workspace" in raw:
        kwargs["workspace"] = Path(raw["workspace"]).expanduser()
    return Config(**kwargs)
