"""Skills discovery — scan workspace/skills/ for SKILL.md files."""

from __future__ import annotations

import re
from pathlib import Path


class SkillsLoader:
    """Discover and summarise skills for the system prompt."""

    def __init__(self, workspace: Path):
        self._dir = workspace / "skills"

    def list_skills(self) -> list[dict[str, str]]:
        """Return [{name, path, description}, ...] for each discovered skill."""
        if not self._dir.is_dir():
            return []
        skills: list[dict[str, str]] = []
        for d in sorted(self._dir.iterdir()):
            if not d.is_dir():
                continue
            md = d / "SKILL.md"
            if md.exists():
                desc = self._extract_description(md)
                skills.append({"name": d.name, "path": str(md), "description": desc})
        return skills

    def build_summary(self) -> str:
        """Build a concise summary for the system prompt."""
        skills = self.list_skills()
        if not skills:
            return ""
        lines: list[str] = []
        for s in skills:
            lines.append(f"- **{s['name']}**: {s['description']}\n  Path: {s['path']}")
        return "\n".join(lines)

    # ------------------------------------------------------------------

    @staticmethod
    def _extract_description(path: Path) -> str:
        """Extract description from SKILL.md — first non-empty, non-heading line."""
        try:
            text = path.read_text(encoding="utf-8")
            # Strip optional YAML frontmatter
            if text.startswith("---"):
                m = re.match(r"^---\n.*?\n---\n", text, re.DOTALL)
                if m:
                    text = text[m.end():]
            for line in text.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    return line[:120]
            return path.parent.name
        except Exception:
            return path.parent.name
