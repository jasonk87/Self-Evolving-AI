"""Incremental connector sync. No cursor or freshness advances on partial failure."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Protocol

from .repository import FactsRepository


@dataclass
class FactBatch:
    items: list[dict] = field(default_factory=list)
    cursor: str | None = None
    full_snapshot: bool = False


class FactSource(Protocol):
    id: str
    entity: str

    async def fetch(self, cursor: str | None) -> FactBatch: ...


class FactsSync:
    def __init__(self, repository: FactsRepository, settings, bus):
        self.repository, self.store, self.settings, self.bus = repository, repository.store, settings, bus
        self.sources: dict[str, FactSource] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._task: asyncio.Task | None = None

    def register(self, source: FactSource) -> None:
        self.repository.table(source.entity)
        if source.id in self.sources and self.sources[source.id] is not source:
            raise ValueError("Source id already registered")
        self.sources[source.id] = source
        self._locks.setdefault(source.id, asyncio.Lock())
        self.store.execute("INSERT INTO facts_sources(id,entity) VALUES(?,?) ON CONFLICT(id) DO NOTHING", (source.id, source.entity))

    async def sync(self, source_id: str, *, force: bool = False) -> dict:
        if source_id not in self.sources:
            return {"source": source_id, "ok": False, "error": "Source is not connected"}
        async with self._locks[source_id]:
            source = self.sources[source_id]
            state = self.store.query_one("SELECT * FROM facts_sources WHERE id=?", (source_id,))
            ttl = self.settings.get("facts.ttl_seconds")
            if not force and not state["error"] and state["last_success_at"] is not None and time.time() - state["last_success_at"] < ttl:
                return {"source": source_id, "ok": True, "changed": 0, "fetched_at": state["last_success_at"]}
            attempted_at = time.time()
            self.store.execute("UPDATE facts_sources SET last_attempt_at=? WHERE id=?", (attempted_at, source_id))
            try:
                batch = await asyncio.wait_for(source.fetch(state["cursor"]), timeout=self.settings.get("facts.sync_timeout_seconds"))
                fetched_at = time.time()
                changed = 0
                with self.store.transaction():
                    for values in batch.items:
                        _, kind = self.repository.upsert(source.entity, values, source=source_id,
                                                         fetched_at=fetched_at, ttl_seconds=ttl)
                        changed += bool(kind)
                    if batch.full_snapshot and source.entity == "events":
                        present = {str(v["external_id"]) for v in batch.items}
                        old = self.store.query("SELECT external_id,status FROM facts_events WHERE source=?", (source_id,))
                        for row in old:
                            if row["external_id"] not in present and row["status"] != "cancelled":
                                self.repository.upsert("events", {"external_id": row["external_id"], "status": "cancelled"},
                                                       source=source_id, fetched_at=fetched_at, ttl_seconds=ttl)
                                changed += 1
                    # An incremental response certifies unchanged records too, but only
                    # after the entire paginated delta has succeeded.
                    self.store.execute("UPDATE facts_provenance SET fetched_at=?,ttl_seconds=? WHERE source=?",
                                       (fetched_at, ttl, source_id))
                    if source.entity in {"events", "threads"}:
                        self.store.execute(f"UPDATE {self.repository.table(source.entity)} SET fetched_at=? WHERE source=?",
                                           (fetched_at, source_id))
                    self.store.execute("UPDATE facts_sources SET cursor=?,last_success_at=?,error='' WHERE id=?",
                                       (batch.cursor, fetched_at, source_id))
                self.bus.publish("facts.synced", {"source": source_id, "entity": source.entity, "changed": changed})
                return {"source": source_id, "ok": True, "changed": changed, "fetched_at": fetched_at}
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Do not store exception URLs: connector URLs can carry credentials.
                error = f"Source refresh failed ({type(exc).__name__}); cached facts are not verified."
                self.store.execute("UPDATE facts_sources SET error=? WHERE id=?", (error, source_id))
                self.bus.publish("facts.sync_failed", {"source": source_id, "error": error})
                return {"source": source_id, "ok": False, "error": error}

    async def refresh(self, entity: str, source: str | None = None, *, force: bool = False) -> list[dict]:
        known = {r["id"] for r in self.store.query("SELECT id FROM facts_sources WHERE entity=?", (entity,))}
        known.update(r["source"] for r in self.store.query("SELECT DISTINCT source FROM facts_provenance WHERE entity=?", (entity,))
                     if not r["source"].startswith("local:"))
        if source is not None:
            known = {source} if not source.startswith("local:") else set()
        if entity in {"events", "threads"} and not known:
            return [{"source": None, "ok": False, "error": "No source is connected"}]
        return list(await asyncio.gather(*(self.sync(s, force=force) for s in sorted(known))))

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="facts-sync")

    async def _run(self) -> None:
        while True:
            for source_id in list(self.sources):
                await self.sync(source_id)
            await asyncio.sleep(self.settings.get("facts.sync_interval_seconds"))

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
