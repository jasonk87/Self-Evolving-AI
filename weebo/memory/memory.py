"""Long-term memory: what Weebo knows about the user, their world, and itself.

Recall is hybrid: SQLite full-text search (exact words) fused with semantic search over local embeddings
(meaning; embeddings.py) when that's enabled and the model is loaded. Embedding happens in a background
thread, so a turn never waits for the model; until it's ready, recall is keyword-only.
"""

from __future__ import annotations

import hashlib
import json
import re
import statistics
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import log, paths
from . import embeddings

if TYPE_CHECKING:
    from ..events import EventBus
    from ..store import Store

logger = log.get("memory")

KINDS = ("preference", "fact", "person", "goal", "project", "lesson", "episode", "insight")
# Small embedding models put unrelated sentences at cosine ~0.5, so a meaning-only match has to stand out from
# the rest of memory (z-score), not just pass a fixed similarity. With only a few memories there's no
# distribution to compare against, so a fixed cutoff applies instead.
SEMANTIC_FLOOR = 0.5
SEMANTIC_MIN_Z = 1.9
SEMANTIC_SMALL_SET = 8
SEMANTIC_MIN_SIMILARITY = 0.55
RRF_K = 60  # reciprocal rank fusion constant
INDEX_BATCH = 32

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
    def __init__(self, store: "Store", bus: "EventBus", embedder: "embeddings.Embedder | None" = None):
        self.store = store
        self.bus = bus
        self.embedder = embedder
        self.semantic = "off"  # off | unavailable | loading | ready | error
        self._vectors: dict[str, list[float]] = {}
        self._vectors_lock = threading.Lock()
        self._wake = threading.Event()
        self._worker: threading.Thread | None = None

    # ---------------------------------------------------------------- semantic index
    def start_indexing(self, enabled: bool) -> None:
        """Load the embedding model and embed memories in the background (never on the event loop)."""
        if not enabled:
            self.stop()
            return
        if self.embedder is None:
            if not embeddings.available():
                self.semantic = "unavailable"
                return
            self.embedder = embeddings.FastEmbedder()
        self.semantic = "loading" if self.semantic != "ready" else "ready"
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._index_loop, name="memory-index", daemon=True)
            self._worker.start()
        self._wake.set()

    def stop(self, timeout: float = 5.0) -> None:
        """Turn semantic recall off and let the indexer thread finish (before the Store it reads is closed)."""
        self.semantic = "off"
        self._wake.set()
        worker = self._worker
        if worker and worker.is_alive() and worker is not threading.current_thread():
            worker.join(timeout)

    def _index_loop(self) -> None:
        try:
            self.embedder.warm()  # type: ignore[union-attr]
            model = self.embedder.name  # type: ignore[union-attr]
            with self._vectors_lock:
                self._vectors = {r["memory_id"]: embeddings.unpack(r["vector"]) for r in self.store.memory_vectors(model)}
        except Exception as exc:
            logger.warning("Semantic memory unavailable: %s", exc)
            self.semantic = "error"
            return
        while self.semantic not in ("off", "error"):
            try:
                self.index_pending()
                if self.semantic == "loading":
                    self.semantic = "ready"
                    logger.info("Semantic memory recall ready (%d memories embedded)", len(self._vectors))
            except Exception as exc:  # a bad batch must not kill recall for good
                logger.warning("Embedding memories failed: %s", exc)
            self._wake.wait(300)
            self._wake.clear()

    def index_pending(self) -> int:
        """Embed active memories that have no vector yet (new or edited). Returns how many were embedded."""
        if self.embedder is None:
            return 0
        done = 0
        while self.semantic != "off":
            rows = self.store.memories_needing_vectors(self.embedder.name, limit=INDEX_BATCH)
            if not rows:
                return done
            vectors = self.embedder.embed([r["text"] for r in rows])  # slow: the text may change meanwhile
            stored_any = False
            for row, vector in zip(rows, vectors):
                text_hash = hashlib.sha1(row["text"].encode("utf-8")).hexdigest()[:16]
                with self._vectors_lock:  # store + cache together, so an edit can't slip in between them
                    # Refused if the memory was edited or forgotten mid-embedding; the next pass sees the new text.
                    if self.store.set_memory_vector(row["id"], self.embedder.name, row["text"], text_hash,
                                                    embeddings.pack(vector)):
                        self._vectors[row["id"]] = vector
                        done += 1
                        stored_any = True
            if not stored_any:
                return done  # everything changed under us; those edits already woke the loop for another pass
        return done

    def _changed(self, memory_id: str) -> None:
        """Call after the store changed a memory's text (its vector row is already gone)."""
        with self._vectors_lock:
            self._vectors.pop(memory_id, None)
        if self.semantic in ("loading", "ready"):
            self._wake.set()

    def _semantic_hits(self, query: str, limit: int) -> list[tuple[str, float]]:
        if self.semantic != "ready" or self.embedder is None or not query.strip():
            return []
        try:
            probe = self.embedder.embed([query])[0]
        except Exception as exc:
            logger.debug("query embedding failed: %s", exc)
            return []
        with self._vectors_lock:
            scored = [(mid, embeddings.dot(probe, vec)) for mid, vec in self._vectors.items()]
        if len(scored) >= SEMANTIC_SMALL_SET:
            mean = statistics.fmean(s for _, s in scored)
            spread = statistics.pstdev(s for _, s in scored) or 1.0
            scored = [(m, s) for m, s in scored if s >= SEMANTIC_FLOOR and (s - mean) / spread >= SEMANTIC_MIN_Z]
        else:
            scored = [(m, s) for m, s in scored if s >= SEMANTIC_MIN_SIMILARITY]
        scored.sort(key=lambda s: s[1], reverse=True)
        return scored[:limit]

    def search(self, query: str, limit: int = 8, kinds: list[str] | None = None) -> list[dict[str, Any]]:
        """Hybrid recall: keyword hits and meaning hits merged by reciprocal rank fusion."""
        keyword = self.store.search_memories(query, limit=limit * 2, kinds=kinds)
        semantic = self._semantic_hits(query, limit * 2)
        if not semantic:
            return keyword[:limit]
        rows = {m["id"]: m for m in keyword}
        scores: dict[str, float] = {}
        for rank, m in enumerate(keyword):
            scores[m["id"]] = scores.get(m["id"], 0.0) + 1.0 / (RRF_K + rank)
        for rank, (mid, similarity) in enumerate(semantic):
            if mid not in rows:
                row = self.store.get_memory(mid)
                if not row or row["status"] != "active" or (kinds and row["kind"] not in kinds):
                    continue
                rows[mid] = row
            rows[mid]["similarity"] = round(similarity, 3)
            scores[mid] = scores.get(mid, 0.0) + 1.0 / (RRF_K + rank)
        for mid, row in rows.items():  # same tie-breaks the keyword ranking uses: pinned, then importance
            scores[mid] += (0.01 if row["pinned"] else 0.0) + 0.001 * row["importance"]
        return [rows[mid] for mid in sorted(scores, key=scores.get, reverse=True)[:limit]]

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
            if "text" in updates:
                self._changed(existing["id"])
            self.bus.publish("memory.updated", {"memory": mem})
            return mem, "updated"  # type: ignore[return-value]
        mem = self.store.add_memory(text, kind, importance, source, pinned, meta)
        self._changed(mem["id"])
        self.bus.publish("memory.added", {"memory": mem})
        return mem, "added"

    def recall(self, query: str, limit: int = 8, kinds: list[str] | None = None) -> list[dict[str, Any]]:
        results = self.search(query, limit=limit, kinds=kinds)
        self.store.touch_memories(m["id"] for m in results)
        return results

    def forget(self, memory_id: str) -> bool:
        removed = self.store.delete_memory(memory_id)
        if removed:
            self._changed(memory_id)
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
            if "text" in allowed:
                self._changed(memory_id)
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
        related = [m for m in self.search(query, limit=limit + len(seen)) if m["id"] not in seen][:limit]
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
        recall = {"ready": "semantic + keyword", "loading": "keyword (semantic model loading)",
                  "unavailable": "keyword (pip install fastembed for semantic recall)",
                  "error": "keyword (semantic model failed to load)"}.get(self.semantic, "keyword")
        return {"total": sum(r["n"] for r in rows), "by_kind": {r["kind"]: r["n"] for r in rows},
                "recall": recall, "semantic": self.semantic, "embedded": len(self._vectors)}

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
