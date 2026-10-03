"""Learned skills: procedures Weebo figured out, saved as Codex-native SKILL.md files.

Weebo registers its skills folder as an extra Codex skill root, so every future
thread (chat or agent) sees them and can follow them. This is the low-risk half
of self-evolution: new abilities without touching Weebo's own code.
"""

from __future__ import annotations

import re
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import log, paths
from .memory.memory import looks_secret

if TYPE_CHECKING:
    from .app import WeeboApp

logger = log.get("skills")

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,47}$")


def _frontmatter(text: str) -> dict[str, str]:
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, flags=re.DOTALL)
    if not match:
        return {}
    meta: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip().strip('"')
    return meta


class Skills:
    def __init__(self, app: "WeeboApp"):
        self.app = app
        self.root = paths.skills_dir()

    async def sync_with_codex(self) -> None:
        await self.app.engine.set_skill_roots([str(self.root)])

    async def save(self, name: str, description: str, instructions: str, source: str = "") -> dict[str, Any]:
        name = (name or "").strip().lower().replace(" ", "-")
        description = " ".join((description or "").split())
        if not NAME_RE.match(name):
            raise ValueError("Skill name must be kebab-case: 2-48 characters, a-z, 0-9 and dashes.")
        if not description or len(description) > 300:
            raise ValueError("Description must be one line under 300 characters.")
        if not instructions or len(instructions) > 20000:
            raise ValueError("Instructions must be between 1 and 20,000 characters.")
        if looks_secret(instructions) or looks_secret(description):
            raise ValueError("Skills can't contain secrets (passwords, keys, card numbers).")
        folder = self.root / name
        existed = folder.exists()
        folder.mkdir(parents=True, exist_ok=True)
        body = instructions.strip()
        content = f"---\nname: {name}\ndescription: {description.replace(chr(10), ' ')}\n---\n\n{body}\n"
        (folder / "SKILL.md").write_text(content, encoding="utf-8")
        skill = {"name": name, "description": description, "path": str(folder / "SKILL.md"),
                 "updated_at": time.time(), "source": source}
        self.app.store.journal("skill", f"{'Updated' if existed else 'Learned'} skill: {name}", description,
                               {"source": source})
        self.app.bus.publish("skills.updated", {"skill": skill})
        await self.sync_with_codex()
        return skill

    def list(self) -> list[dict[str, Any]]:
        skills = []
        for skill_file in sorted(self.root.glob("*/SKILL.md")):
            try:
                text = skill_file.read_text(encoding="utf-8")
            except OSError:
                continue
            meta = _frontmatter(text)
            body = re.sub(r"^---.*?---\s*", "", text, count=1, flags=re.DOTALL)
            skills.append({
                "name": meta.get("name") or skill_file.parent.name,
                "description": meta.get("description", ""),
                "path": str(skill_file),
                "updated_at": skill_file.stat().st_mtime,
                "preview": body[:600],
            })
        return skills

    def read(self, name: str) -> str:
        path = self.root / name / "SKILL.md"
        if not NAME_RE.match(name) or not path.exists():
            raise ValueError(f"No skill named {name}")
        return path.read_text(encoding="utf-8")

    async def delete(self, name: str) -> bool:
        folder = self.root / name
        if not NAME_RE.match(name) or not folder.exists():
            return False
        shutil.rmtree(folder, ignore_errors=True)
        self.app.store.journal("skill", f"Forgot skill: {name}")
        self.app.bus.publish("skills.updated", {"deleted": name})
        await self.sync_with_codex()
        return True

    def seed_defaults(self) -> None:
        """Ship a starter skill so the mechanism is visible from day one."""
        folder = self.root / "weebo-self-check"
        if folder.exists():
            return
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "SKILL.md").write_text(
            "---\nname: weebo-self-check\ndescription: Verify Weebo 2.0's own code still boots and passes its tests "
            "after a change.\n---\n\n"
            f"1. From `{paths.PROJECT_ROOT}` run `python -m weebo --selftest`.\n"
            "2. Run `python -m pytest tests/weebo -q -p no:cacheprovider`.\n"
            "3. If anything fails, read the failure, fix the cause, and run both again.\n"
            "4. Report which checks passed.\n",
            encoding="utf-8",
        )
