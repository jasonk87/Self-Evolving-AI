"""Long-term memory: what Weebo knows about the user, their world, and itself."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import log, paths

if TYPE_CHECKING:
    from ..events import EventBus
    from ..store import Store

logger = log.get("memory")

KINDS = ("preference", "fact", "person", "goal", "project", "lesson", "episode", "insight")

_SECRET_PATTERNS = [
    re.compile(r"\b(sk|pk|rk|ghp|gho|xox[abp]|AIza)[-_A-Za-z0-9]{16,}"),
    re.compile(r"(?i)\b(password|passwd|passcode|api[_ -]?key|secret|token|pin)\b\s*(is|:|=)\s*\S+"),
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),  # SSN-like
    re.compile(r"\b(?:\d{4}[ -]){3}\d{1,4}\b|\b\d{15,16}\b"),  # card-number-like
]


class MemoryError_(ValueError):
    pass


def looks_secret(text: str) -> bool:
    return any(p.search(text) for p in _SECRET_PATTERNS)


class Memory:
    def __init__(self, store: "Store", bus: "EventBus"):
        self.store = store
        self.bus = bus

    def remember(self, text: str, kind: str = "fact", importance: int = 3, source: str = "chat",
                 pinned: bool = False, meta: dict | None = None) -> tuple[dict[str, Any], str]:
        text = " ".join((text or "").split())
        if len(text) < 3:
            raise MemoryError_("Memory text is too short.")
        if len(text) > 1200:
            raise MemoryError_("Memory text is too long; store one concise statement (under 1200 characters).")
        if looks_secret(text):
            raise MemoryError_("That looks like a secret (password, key or card number). Weebo never stores secrets.")
        kind = kind if kind in KINDS else "fact"
        existing = self.store.find_similar_memory(text)
        if existing:
            updates: dict[str, Any] = {"importance": max(existing["importance"], int(importance))}
            if len(text) > len(existing["text"]):
                updates["text"] = text
            if existing["status"] != "active":
                updates["status"] = "active"
            mem = self.store.update_memory(existing["id"], **updates)
            self.bus.publish("memory.updated", {"memory": mem})
            return mem, "updated"  # type: ignore[return-value]
        mem = self.store.add_memory(text, kind, importance, source, pinned, meta)
        self.bus.publish("memory.added", {"memory": mem})
        return mem, "added"

    def recall(self, query: str, limit: int = 8, kinds: list[str] | None = None) -> list[dict[str, Any]]:
        results = self.store.search_memories(query, limit=limit, kinds=kinds)
        self.store.touch_memories(m["id"] for m in results)
        return results

    def forget(self, memory_id: str) -> bool:
        removed = self.store.delete_memory(memory_id)
        if removed:
            self.bus.publish("memory.deleted", {"id": memory_id})
        return removed

    def update(self, memory_id: str, **values: Any) -> dict[str, Any] | None:
        allowed = {k: v for k, v in values.items() if k in ("text", "kind", "importance", "pinned", "status") and v is not None}
        if "text" in allowed and looks_secret(allowed["text"]):
            raise MemoryError_("That looks like a secret; not stored.")
        if "kind" in allowed and allowed["kind"] not in KINDS:
            raise MemoryError_(f"kind must be one of {', '.join(KINDS)}")
        mem = self.store.update_memory(memory_id, **allowed)
        if mem:
            self.bus.publish("memory.updated", {"memory": mem})
        return mem

    def profile(self, limit: int = 8) -> list[dict[str, Any]]:
        """Always-on core memories: pinned items plus the most important user facts."""
        rows = self.store.query(
            "SELECT * FROM memories WHERE status='active' AND (pinned=1 OR (importance>=4 AND kind IN "
            "('preference','person','goal','fact'))) ORDER BY pinned DESC, importance DESC, updated_at DESC LIMIT ?",
            (limit,),
        )
        return rows

    def context_block(self, query: str, limit: int = 6) -> tuple[str, list[str]]:
        profile = self.profile()
        seen = {m["id"] for m in profile}
        related = [m for m in self.store.search_memories(query, limit=limit + len(seen)) if m["id"] not in seen][:limit]
        used = [m["id"] for m in related]
        if related:
            self.store.touch_memories(used)
        lines: list[str] = []
        if profile:
            lines.append("Core memories about the user:")
            lines += [f"- [{m['id']}] ({m['kind']}) {m['text']}" for m in profile]
        if related:
            lines.append("Memories related to this message:")
            lines += [f"- [{m['id']}] ({m['kind']}) {m['text']}" for m in related]
        return "\n".join(lines), used

    def stats(self) -> dict[str, Any]:
        rows = self.store.query("SELECT kind, COUNT(*) AS n FROM memories WHERE status='active' GROUP BY kind")
        return {"total": sum(r["n"] for r in rows), "by_kind": {r["kind"]: r["n"] for r in rows}}

    # ---------------------------------------------------------------- legacy
    def import_legacy(self, force: bool = False) -> dict[str, int]:
        """One-time import of Weebo 1.x facts and episodes from ai_assistant/core/data."""
        if self.store.kv_get("legacy_import_done") and not force:
            return {"facts": 0, "episodes": 0, "skipped": 0}
        data_dir = paths.PROJECT_ROOT / "ai_assistant" / "core" / "data"
        counts = {"facts": 0, "episodes": 0, "skipped": 0}
        counts.update(self._import_facts(data_dir / "learned_facts.json"))
        counts["episodes"] = self._import_episodes(data_dir / "episodic_memories.json")
        self.store.kv_set("legacy_import_done", {"at": time.time(), **counts})
        if counts["facts"] or counts["episodes"]:
            logger.info("Imported %s facts and %s episodes from Weebo 1.x", counts["facts"], counts["episodes"])
            self.store.journal("memory", "Imported memories from Weebo 1.x",
                               f"{counts['facts']} facts and {counts['episodes']} conversation episodes.")
        return counts

    def _import_facts(self, path: Path) -> dict[str, int]:
        counts = {"facts": 0, "skipped": 0}
        if not path.exists():
            return counts
        try:
            facts = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return counts
        mapping = {
            "user_preference": ("preference", 4), "user_personal_info": ("fact", 4), "user_statement": ("fact", 3),
            "interaction_rule": ("preference", 3), "interaction_strategy": ("preference", 3),
            "operational_directive": ("preference", 3), "correction": ("lesson", 3),
            "domain_knowledge": ("fact", 2), "manual": ("fact", 3), "technical_term": ("fact", 2),
        }
        for fact in facts if isinstance(facts, list) else []:
            text = str(fact.get("text", "")).strip()
            category = str(fact.get("category", ""))
            if category not in mapping:
                counts["skipped"] += 1
                continue
            text = re.sub(r"\s*\(Evidence:.*$", "", text).strip()
            if len(text) < 6 or looks_secret(text):
                counts["skipped"] += 1
                continue
            kind, importance = mapping[category]
            try:
                _, action = self.remember(text, kind, importance, source="weebo-1.x")
            except MemoryError_:
                counts["skipped"] += 1
                continue
            if action == "added":
                counts["facts"] += 1
        return counts

    def _import_episodes(self, path: Path) -> int:
        if not path.exists():
            return 0
        try:
            episodes = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return 0
        added = 0
        for episode in episodes if isinstance(episodes, list) else []:
            summary = str(episode.get("summary", "")).strip()
            title = str(episode.get("title", "")).strip().rstrip(".")
            if len(summary) < 20:
                continue
            text = f"{title}: {summary}" if title else summary
            try:
                _, action = self.remember(text[:1200], "episode", 2, source="weebo-1.x")
            except MemoryError_:
                continue
            if action == "added":
                added += 1
        return added
