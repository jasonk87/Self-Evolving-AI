"""Learned skills: procedures Weebo figured out, saved as Codex-native SKILL.md files.

Weebo registers its skills folder as an extra Codex skill root, so every future
thread (chat or agent) sees them and can follow them. This is the low-risk half
of self-evolution: new abilities without touching Weebo's own code.

Skills compound only if the useful ones are kept and the rest don't pile up: Weebo
counts a use whenever a chat or agent reads a skill's SKILL.md, dreams suggest new
skills from agent work that repeats, and auto-learned skills nobody has used in
``skills.prune_unused_days`` are moved to an archive folder (not deleted).
"""

from __future__ import annotations

import asyncio
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
SKILL_FILE_RE = re.compile(r"skills[\\/]+([a-z0-9][a-z0-9-]{1,47})[\\/]+SKILL\.md", re.IGNORECASE)
META_KEY = "skill_meta"
SEEDED = {"weebo-self-check"}
AUTO_LEARNED = "dream"  # the source of skills Weebo learns on its own; the only ones it may archive
PRUNE_EVERY = 86400


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
        meta = self._meta()
        meta[name] = {**meta.get(name, {"created_at": time.time(), "uses": 0}), "source": source or "chat"}
        self._save_meta(meta)
        skill = {"name": name, "description": description, "path": str(folder / "SKILL.md"),
                 "updated_at": time.time(), "source": source}
        self.app.store.journal("skill", f"{'Updated' if existed else 'Learned'} skill: {name}", description,
                               {"source": source})
        self.app.bus.publish("skills.updated", {"skill": skill})
        await self.sync_with_codex()
        return skill

    def list(self) -> list[dict[str, Any]]:
        skills = []
        usage = self._meta()
        for skill_file in sorted(self.root.glob("*/SKILL.md")):
            try:
                text = skill_file.read_text(encoding="utf-8")
            except OSError:
                continue
            meta = _frontmatter(text)
            body = re.sub(r"^---.*?---\s*", "", text, count=1, flags=re.DOTALL)
            used = usage.get(skill_file.parent.name, {})
            skills.append({
                "name": meta.get("name") or skill_file.parent.name,
                "description": meta.get("description", ""),
                "path": str(skill_file),
                "updated_at": skill_file.stat().st_mtime,
                "preview": body[:600],
                "uses": int(used.get("uses", 0)),
                "last_used_at": used.get("last_used_at"),
                "source": used.get("source", ""),
            })
        return skills

    # ------------------------------------------------------------------ usage and upkeep
    def _meta(self) -> dict[str, dict[str, Any]]:
        return self.app.store.kv_get(META_KEY, {}) or {}

    def _save_meta(self, meta: dict[str, dict[str, Any]]) -> None:
        self.app.store.kv_set(META_KEY, meta)

    def note_item(self, item: dict[str, Any]) -> list[str]:
        """Count a use when a chat or agent reads one of Weebo's SKILL.md files (that's how Codex follows one)."""
        if item.get("type") != "commandExecution":
            return []
        texts = [str(item.get("command") or "")]
        texts += [str(a.get("path") or "") for a in item.get("commandActions") or [] if isinstance(a, dict)]
        names = sorted({m.group(1).lower() for text in texts for m in SKILL_FILE_RE.finditer(text)})
        used = [n for n in names if (self.root / n / "SKILL.md").exists()]
        if used:
            meta, now = self._meta(), time.time()
            for name in used:
                entry = meta.setdefault(name, {"created_at": now, "uses": 0, "source": ""})
                entry["uses"] = int(entry.get("uses", 0)) + 1
                entry["last_used_at"] = now
            self._save_meta(meta)
            self.app.bus.publish("skills.updated", {"used": used})
        return used

    def prune(self, force: bool = False) -> list[str]:
        """Archive skills Weebo learned on its own while dreaming that went unused for ``skills.prune_unused_days``
        (0 = never). Runs at most daily. Skills saved in a chat may be ones the user asked for, and skills from
        before usage was tracked have no known origin, so those are never archived automatically."""
        days = int(self.app.settings.get("skills.prune_unused_days") or 0)
        store, now = self.app.store, time.time()
        tracking_since = float(store.kv_get("skill_tracking_since") or 0)
        if not tracking_since:
            store.kv_set("skill_tracking_since", now)  # usage before this point was never counted
            return []
        if days <= 0 or (not force and now - float(store.kv_get("last_skill_prune") or 0) < PRUNE_EVERY):
            return []
        store.kv_set("last_skill_prune", now)
        cutoff, meta, archived = now - days * 86400, self._meta(), []
        for skill_file in self.root.glob("*/SKILL.md"):
            name = skill_file.parent.name
            entry = meta.get(name, {})
            if name in SEEDED or entry.get("source") != AUTO_LEARNED:
                continue
            seen = max(float(entry.get("last_used_at") or 0), float(entry.get("created_at") or 0),
                       skill_file.stat().st_mtime, tracking_since)
            if seen < cutoff:
                target = paths.sub("skills_archive") / f"{name}-{int(now)}"
                try:
                    shutil.move(str(skill_file.parent), str(target))
                except OSError as exc:
                    logger.warning("Couldn't archive skill %s: %s", name, exc)
                    continue
                meta.pop(name, None)
                archived.append(name)
        if archived:
            self._save_meta(meta)
            store.journal("skill", f"Archived {len(archived)} unused skill(s)",
                          f"Not used in {days} days: {', '.join(archived)}. They're in weebo_data/skills_archive.")
            self.app.bus.publish("skills.updated", {"archived": archived})
            try:
                asyncio.get_running_loop().create_task(self.sync_with_codex())
            except RuntimeError:
                pass  # no loop (called from a script): Codex rescans its skill roots on the next start
        return archived

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
        meta = self._meta()
        if meta.pop(name, None) is not None:
            self._save_meta(meta)
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
