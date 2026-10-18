"""Atomic facts, provenance, change history and artifact invalidation."""

from __future__ import annotations

import json
import time
from typing import Any
from zoneinfo import ZoneInfo

from ..store import Store, new_id
from .compute import instant

ENTITIES = {"events", "threads", "commitments", "people"}
JSON_FIELDS = {"attendees", "raw", "participants", "aliases", "emails", "before_json", "after_json"}
FIELDS = {
    "events": {"title", "start", "end", "tz", "location", "attendees", "status", "version", "raw"},
    "threads": {"subject", "participants", "last_message_at", "needs_reply", "summary"},
    "commitments": {"text", "owner", "due", "status", "source_ref", "created_at"},
    "people": {"name", "aliases", "emails", "notes_ref", "tz"},
}


def _decode(row: dict | None) -> dict | None:
    if row is None:
        return None
    return {key: json.loads(value) if key in JSON_FIELDS and isinstance(value, str) else value
            for key, value in row.items()}


class FactsRepository:
    def __init__(self, store: Store):
        self.store = store

    @staticmethod
    def table(entity: str) -> str:
        if entity not in ENTITIES:
            raise ValueError("Unknown fact entity")
        return "facts_" + entity

    def get(self, entity: str, fact_id: str) -> dict | None:
        row = _decode(self.store.query_one(f"SELECT * FROM {self.table(entity)} WHERE id=?", (fact_id,)))
        if row:
            row["provenance"] = self.store.query_one(
                "SELECT source, fetched_at, ttl_seconds FROM facts_provenance WHERE entity=? AND entity_id=?",
                (entity, fact_id))
        return row

    def upsert(self, entity: str, values: dict, *, source: str, fetched_at: float | None = None,
               ttl_seconds: float = 300) -> tuple[dict, str | None]:
        table = self.table(entity)
        fetched_at = time.time() if fetched_at is None else instant(fetched_at)
        if not source or not isinstance(source, str):
            raise ValueError("Facts require a source")
        if instant(ttl_seconds) <= 0:
            raise ValueError("TTL must be positive")
        values = dict(values)
        if set(values) - FIELDS[entity] - {"id", "external_id"}:
            raise ValueError("Unexpected fact fields")
        external_id = str(values.pop("external_id", ""))
        if entity in {"events", "threads"} and not external_id:
            raise ValueError("Connector facts require an external id")
        with self.store.transaction():
            previous = None
            if external_id:
                previous = _decode(self.store.query_one(f"SELECT * FROM {table} WHERE source=? AND external_id=?",
                                                        (source, external_id)))
            elif values.get("id"):
                previous = _decode(self.store.query_one(f"SELECT * FROM {table} WHERE id=?", (values["id"],)))
            fact_id = previous["id"] if previous else values.pop("id", None) or new_id("fact")
            if previous:
                # A source cannot overwrite another source's person or local commitment.
                provenance = self.get(entity, fact_id)["provenance"]
                if provenance and provenance["source"] != source:
                    raise ValueError("Fact belongs to another source")
            merged = {**(previous or {}), **values, "id": fact_id}
            if entity in {"events", "threads"}:
                merged.update(source=source, external_id=external_id, fetched_at=fetched_at)
            self._validate(entity, merged)
            changed = previous is None or any(merged.get(k) != previous.get(k) for k in FIELDS[entity])
            kind = None
            if changed:
                kind = "added" if previous is None else "changed"
                if previous and entity == "events":
                    if merged["status"] == "cancelled":
                        kind = "cancelled"
                    elif any(merged[k] != previous[k] for k in ("start", "end", "tz")):
                        kind = "moved"
                columns = list(merged)
                params = [json.dumps(merged[c], ensure_ascii=False) if c in JSON_FIELDS else merged[c] for c in columns]
                self.store.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)}) "
                                   f"ON CONFLICT(id) DO UPDATE SET {','.join(c+'=excluded.'+c for c in columns if c != 'id')}", params)
                self.store.execute("INSERT INTO facts_changes(entity,entity_id,change_kind,before_json,after_json,detected_at) "
                                   "VALUES(?,?,?,?,?,?)", (entity, fact_id, kind,
                                   json.dumps(previous) if previous else None, json.dumps(merged), fetched_at))
                self.store.execute("UPDATE facts_artifacts SET stale=1 WHERE id IN "
                                   "(SELECT artifact_id FROM facts_dependencies WHERE entity=? AND entity_id=?)",
                                   (entity, fact_id))
            elif entity in {"events", "threads"}:
                self.store.execute(f"UPDATE {table} SET fetched_at=? WHERE id=?", (fetched_at, fact_id))
            self.store.execute("INSERT INTO facts_provenance(entity,entity_id,source,fetched_at,ttl_seconds) VALUES(?,?,?,?,?) "
                               "ON CONFLICT(entity,entity_id) DO UPDATE SET source=excluded.source, "
                               "fetched_at=excluded.fetched_at,ttl_seconds=excluded.ttl_seconds",
                               (entity, fact_id, source, fetched_at, ttl_seconds))
            return self.get(entity, fact_id), kind

    @staticmethod
    def _validate(entity: str, row: dict) -> None:
        if entity == "events":
            for key, default in {"location": "", "attendees": [], "status": "confirmed", "version": "", "raw": {}}.items():
                row.setdefault(key, default)
            if not isinstance(row.get("title"), str) or not row.get("tz"):
                raise ValueError("Events require title and timezone")
            ZoneInfo(row["tz"])
            row["start"], row["end"] = instant(row["start"]), instant(row["end"])
            if row["end"] < row["start"]:
                raise ValueError("Event end precedes its start")
            if row["status"] not in {"confirmed", "tentative", "cancelled"}:
                raise ValueError("Unknown event status")
        elif entity == "threads":
            row.setdefault("participants", [])
            row.setdefault("summary", "")
            row.setdefault("needs_reply", False)
            if not isinstance(row.get("subject"), str) or row["needs_reply"] not in (True, False, 0, 1):
                raise ValueError("Invalid email thread")
            row["last_message_at"] = instant(row["last_message_at"])
            row["needs_reply"] = int(row["needs_reply"])
        elif entity == "commitments":
            row.setdefault("status", "open")
            row.setdefault("due", None)
            row.setdefault("created_at", time.time())
            if not row.get("text") or not row.get("source_ref"):
                raise ValueError("Commitments require text and a source reference")
            if row.get("owner") not in {"user", "assistant"} or row["status"] not in {"open", "done", "dropped"}:
                raise ValueError("Invalid commitment owner or status")
            if row["due"] is not None:
                row["due"] = instant(row["due"])
        else:
            if not row.get("name"):
                raise ValueError("People require a name")
            for key, value in {"aliases": [], "emails": [], "notes_ref": "", "tz": ""}.items():
                row.setdefault(key, value)
            if row["tz"]:
                ZoneInfo(row["tz"])
        for key in {"attendees", "participants", "aliases", "emails"} & row.keys():
            if not isinstance(row[key], list):
                raise ValueError(f"{key} must be a list")

    def query(self, entity: str, *, start: float | None = None, end: float | None = None,
              status: str | None = None, needs_reply: bool | None = None, source: str | None = None,
              limit: int = 100, offset: int = 0) -> list[dict]:
        table = self.table(entity)
        clauses, params = [], []
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be a whole number between 1 and 500")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset must be a non-negative whole number")
        if start is not None and entity != "events":
            raise ValueError("start is only supported for events")
        if end is not None and entity not in {"events", "commitments"}:
            raise ValueError("end is only supported for events and commitments")
        if start is not None and end is not None and start >= end:
            raise ValueError("start must precede end")
        if entity == "events":
            if start is not None:
                clauses.append("end > ?")
                params.append(instant(start))
            if end is not None:
                clauses.append("start < ?")
                params.append(instant(end))
        if entity == "commitments" and end is not None:
            clauses.append("due IS NOT NULL AND due <= ?")
            params.append(instant(end))
        if status is not None:
            if entity not in {"events", "commitments"}:
                raise ValueError("This fact type has no status")
            clauses.append("status=?")
            params.append(status)
        if needs_reply is not None:
            if entity != "threads" or not isinstance(needs_reply, bool):
                raise ValueError("needs_reply is only for email threads")
            clauses.append("needs_reply=?")
            params.append(int(needs_reply))
        if source is not None:
            clauses.append("id IN (SELECT entity_id FROM facts_provenance WHERE entity=? AND source=?)")
            params.extend((entity, source))
        order = {"events": "start,id", "threads": "last_message_at DESC,id",
                 "commitments": "due IS NULL,due,id", "people": "name,id"}[entity]
        rows = self.store.query(f"SELECT id FROM {table}" + (" WHERE " + " AND ".join(clauses) if clauses else "")
                                + f" ORDER BY {order} LIMIT ? OFFSET ?", [*params, min(max(int(limit), 1), 500), max(int(offset), 0)])
        return [self.get(entity, r["id"]) for r in rows]

    def changes_since(self, cursor: int = 0, limit: int = 100) -> list[dict]:
        return [_decode(r) for r in self.store.query("SELECT * FROM facts_changes WHERE id>? ORDER BY id LIMIT ?",
                                                     (cursor, min(max(limit, 1), 500)))]

    def save_artifact(self, artifact_id: str, kind: str, data: dict, refs: list[tuple[str, str]]) -> None:
        with self.store.transaction():
            if any(not self.get(entity, fact_id) for entity, fact_id in refs):
                raise ValueError("Artifact references an unknown fact")
            self.store.execute("INSERT INTO facts_artifacts(id,kind,data,stale,updated_at) VALUES(?,?,?,0,?) "
                               "ON CONFLICT(id) DO UPDATE SET kind=excluded.kind,data=excluded.data,stale=0,updated_at=excluded.updated_at",
                               (artifact_id, kind, json.dumps(data), time.time()))
            self.store.execute("DELETE FROM facts_dependencies WHERE artifact_id=?", (artifact_id,))
            for entity, fact_id in refs:
                self.store.execute("INSERT INTO facts_dependencies VALUES(?,?,?)", (artifact_id, entity, fact_id))

    def artifact(self, artifact_id: str, rebuild=None) -> dict | None:
        row = self.store.query_one("SELECT * FROM facts_artifacts WHERE id=?", (artifact_id,))
        if row and row["stale"] and rebuild is not None:
            with self.store.transaction():
                refs = self.store.query("SELECT entity,entity_id FROM facts_dependencies WHERE artifact_id=?", (artifact_id,))
                data = rebuild([self.get(ref["entity"], ref["entity_id"]) for ref in refs])
                self.save_artifact(artifact_id, row["kind"], data, [(r["entity"], r["entity_id"]) for r in refs])
            return self.artifact(artifact_id)
        return row
