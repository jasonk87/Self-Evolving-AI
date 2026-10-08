"""SQLite persistence for conversations, memories, agents, reminders and evolution.

One connection in WAL mode guarded by a lock. Queries are tiny, so calling them
directly from the event loop is cheaper than bouncing through a thread pool.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

from . import paths

SCHEMA_VERSION = 3

V3_SCHEMA = """
CREATE TABLE IF NOT EXISTS eval_cases (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    prompt TEXT NOT NULL,
    rubric TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'user',
    status TEXT NOT NULL DEFAULT 'active',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    data TEXT NOT NULL DEFAULT '{}',
    meta TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_eval_cases_status ON eval_cases(status, created_at DESC);

CREATE TABLE IF NOT EXISTS memory_vectors (
    memory_id TEXT PRIMARY KEY REFERENCES memories(id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    vector BLOB NOT NULL,
    updated_at REAL NOT NULL
);
"""

ROUTINE_SCHEMA = """
CREATE TABLE IF NOT EXISTS routine_occurrences (
    id TEXT PRIMARY KEY,
    reminder_id TEXT NOT NULL,
    due_at REAL NOT NULL,
    conversation_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at REAL NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL,
    data TEXT NOT NULL DEFAULT '{}',
    UNIQUE(reminder_id, due_at)
);
CREATE INDEX IF NOT EXISTS idx_routines_pending ON routine_occurrences(status, next_attempt_at);
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT 'New chat',
    kind TEXT NOT NULL DEFAULT 'chat',
    thread_id TEXT,
    cwd TEXT,
    pinned INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    unread INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    meta TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_conv_updated ON conversations(updated_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT UNIQUE NOT NULL,
    conversation_id TEXT NOT NULL,
    turn_id TEXT,
    role TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'text',
    content TEXT NOT NULL DEFAULT '',
    data TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'done',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id, seq);

CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'fact',
    importance INTEGER NOT NULL DEFAULT 3,
    source TEXT NOT NULL DEFAULT '',
    pinned INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    use_count INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    last_used_at REAL,
    meta TEXT NOT NULL DEFAULT '{}'
);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(text, kind, content='memories', content_rowid='rowid', tokenize='porter unicode61');
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, text, kind) VALUES (new.rowid, new.text, new.kind);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, text, kind) VALUES ('delete', old.rowid, old.text, old.kind);
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE OF text, kind ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, text, kind) VALUES ('delete', old.rowid, old.text, old.kind);
    INSERT INTO memories_fts(rowid, text, kind) VALUES (new.rowid, new.text, new.kind);
END;

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    prompt TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'agent',
    status TEXT NOT NULL DEFAULT 'queued',
    cwd TEXT,
    thread_id TEXT,
    conversation_id TEXT,
    summary TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL,
    meta TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at DESC);

CREATE TABLE IF NOT EXISTS task_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    data TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_task_events ON task_events(task_id, seq);

CREATE TABLE IF NOT EXISTS reminders (
    id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    due_at REAL NOT NULL,
    recurrence TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    conversation_id TEXT,
    action TEXT NOT NULL DEFAULT 'notify',
    created_at REAL NOT NULL,
    fired_at REAL,
    meta TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(status, due_at);

CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    rationale TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'user',
    status TEXT NOT NULL DEFAULT 'proposed',
    branch TEXT,
    worktree TEXT,
    task_id TEXT,
    base_commit TEXT,
    head_commit TEXT,
    merged_commit TEXT,
    diff_stat TEXT NOT NULL DEFAULT '',
    gate_report TEXT NOT NULL DEFAULT '',
    review_report TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    meta TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS journal (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    data TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT UNIQUE NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    data TEXT NOT NULL DEFAULT '{}',
    read INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "can", "could", "did", "do", "does",
    "for", "from", "had", "has", "have", "how", "i", "if", "in", "into", "is", "it", "its", "me",
    "my", "no", "not", "of", "on", "or", "our", "please", "so", "that", "the", "their", "them",
    "then", "there", "these", "they", "this", "to", "up", "us", "was", "we", "were", "what", "when",
    "where", "which", "who", "why", "will", "with", "would", "you", "your", "weebo", "hey", "hi",
    "just", "about", "tell", "know", "get", "got", "want", "like", "some", "any", "all",
}


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str)


def _loads(value: str | None) -> Any:
    if not value:
        return {}
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {}


def fts_query(text: str, max_terms: int = 12) -> str:
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9_'-]*", text.lower())
    seen: list[str] = []
    for word in words:
        word = word.strip("'-_")
        if len(word) < 2 or word in STOPWORDS or word in seen:
            continue
        seen.append(word)
        if len(seen) >= max_terms:
            break
    return " OR ".join(f'"{w}"*' for w in seen)


class Store:
    JSON_FIELDS = {"meta", "data"}

    def __init__(self, path: Path | None = None):
        self.path = path or paths.db_path()
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    # -- plumbing ---------------------------------------------------------
    def _migrate(self) -> None:
        with self._lock:
            version = self._conn.execute("PRAGMA user_version").fetchone()[0]
            if version < 1:
                self._conn.executescript(SCHEMA)
            if version < 2:
                self._conn.executescript(ROUTINE_SCHEMA)
            if version < 3:
                self._conn.executescript(V3_SCHEMA)
            if version < SCHEMA_VERSION:
                self._conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _row(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        out = dict(row)
        for field in self.JSON_FIELDS:
            if field in out and isinstance(out[field], str):
                out[field] = _loads(out[field])
        return out

    def _rows(self, rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
        return [self._row(r) for r in rows]  # type: ignore[misc]

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, tuple(params))

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            return self._rows(self._conn.execute(sql, tuple(params)).fetchall())

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        with self._lock:
            return self._row(self._conn.execute(sql, tuple(params)).fetchone())

    def _insert(self, table: str, values: dict[str, Any]) -> None:
        cols = list(values.keys())
        params = [(_dumps(values[c]) if c in self.JSON_FIELDS else values[c]) for c in cols]
        sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})"
        self.execute(sql, params)

    def _update(self, table: str, key: str, key_value: Any, values: dict[str, Any]) -> None:
        if not values:
            return
        cols = list(values.keys())
        params = [(_dumps(values[c]) if c in self.JSON_FIELDS else values[c]) for c in cols]
        sql = f"UPDATE {table} SET {', '.join(f'{c}=?' for c in cols)} WHERE {key}=?"
        self.execute(sql, [*params, key_value])

    # -- key/value --------------------------------------------------------
    def kv_get(self, key: str, default: Any = None) -> Any:
        row = self.query_one("SELECT value FROM kv WHERE key=?", (key,))
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError:
            return default

    def kv_set(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value, default=str)),
        )

    # -- conversations ----------------------------------------------------
    def create_conversation(self, title: str = "New chat", kind: str = "chat", cwd: str | None = None,
                            meta: dict | None = None, conversation_id: str | None = None) -> dict[str, Any]:
        now = time.time()
        conv = {
            "id": conversation_id or new_id("c"),
            "title": title,
            "kind": kind,
            "cwd": cwd,
            "created_at": now,
            "updated_at": now,
            "meta": meta or {},
            "pinned": 1 if kind == "desk" else 0,
        }
        self._insert("conversations", conv)
        return self.get_conversation(conv["id"])  # type: ignore[return-value]

    def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        return self.query_one("SELECT * FROM conversations WHERE id=?", (conversation_id,))

    def find_conversation_by_kind(self, kind: str) -> dict[str, Any] | None:
        return self.query_one(
            "SELECT * FROM conversations WHERE kind=? AND archived=0 ORDER BY created_at LIMIT 1", (kind,)
        )

    def list_conversations(self, include_archived: bool = False, limit: int = 200) -> list[dict[str, Any]]:
        where = "" if include_archived else "WHERE archived=0"
        return self.query(
            f"SELECT * FROM conversations {where} ORDER BY pinned DESC, updated_at DESC LIMIT ?", (limit,)
        )

    def update_conversation(self, conversation_id: str, **values: Any) -> dict[str, Any] | None:
        values.setdefault("updated_at", time.time())
        self._update("conversations", "id", conversation_id, values)
        return self.get_conversation(conversation_id)

    def delete_conversation(self, conversation_id: str) -> None:
        with self._lock:
            self.execute("DELETE FROM messages WHERE conversation_id=?", (conversation_id,))
            self.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))

    # -- messages ---------------------------------------------------------
    def add_message(self, conversation_id: str, role: str, content: str = "", kind: str = "text",
                    data: dict | None = None, turn_id: str | None = None, status: str = "done",
                    message_id: str | None = None) -> dict[str, Any]:
        now = time.time()
        msg = {
            "id": message_id or new_id("m"),
            "conversation_id": conversation_id,
            "turn_id": turn_id,
            "role": role,
            "kind": kind,
            "content": content,
            "data": data or {},
            "status": status,
            "created_at": now,
            "updated_at": now,
        }
        self._insert("messages", msg)
        self.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now, conversation_id))
        return self.get_message(msg["id"])  # type: ignore[return-value]

    def upsert_message(self, message_id: str, conversation_id: str, role: str, kind: str, content: str,
                       data: dict | None = None, turn_id: str | None = None, status: str = "done") -> dict[str, Any]:
        existing = self.get_message(message_id)
        if existing is None:
            return self.add_message(conversation_id, role, content, kind, data, turn_id, status, message_id)
        self._update("messages", "id", message_id, {
            "content": content, "data": data or {}, "status": status, "updated_at": time.time(),
        })
        return self.get_message(message_id)  # type: ignore[return-value]

    def update_message(self, message_id: str, **values: Any) -> dict[str, Any] | None:
        values.setdefault("updated_at", time.time())
        self._update("messages", "id", message_id, values)
        return self.get_message(message_id)

    def get_message(self, message_id: str) -> dict[str, Any] | None:
        return self.query_one("SELECT * FROM messages WHERE id=?", (message_id,))

    def list_messages(self, conversation_id: str, limit: int = 300, before_seq: int | None = None) -> list[dict[str, Any]]:
        # Reconcile old proposals and any update missed during a restart/reconnect.
        self.list_proposal_messages(conversation_id)
        if before_seq:
            rows = self.query(
                "SELECT * FROM messages WHERE conversation_id=? AND seq<? ORDER BY seq DESC LIMIT ?",
                (conversation_id, before_seq, limit),
            )
        else:
            rows = self.query(
                "SELECT * FROM messages WHERE conversation_id=? ORDER BY seq DESC LIMIT ?", (conversation_id, limit)
            )
        rows.reverse()
        return rows

    def recent_messages(self, conversation_id: str, limit: int = 30) -> list[dict[str, Any]]:
        """Recent rows including the events that separate user and autonomous turns."""
        rows = self.query(
            "SELECT * FROM messages WHERE conversation_id=? ORDER BY seq DESC LIMIT ?",
            (conversation_id, limit),
        )
        rows.reverse()
        return rows

    def recent_dialogue(self, conversation_id: str, limit: int = 30) -> list[dict[str, Any]]:
        rows = self.query(
            "SELECT * FROM messages WHERE conversation_id=? AND kind='text' AND role IN ('user','assistant') "
            "ORDER BY seq DESC LIMIT ?",
            (conversation_id, limit),
        )
        rows.reverse()
        return rows

    def dialogue_since(self, since: float, limit: int = 400) -> list[dict[str, Any]]:
        return self.query(
            "SELECT m.*, c.title AS conversation_title FROM messages m JOIN conversations c ON c.id=m.conversation_id "
            "WHERE m.created_at>? AND m.kind='text' AND m.role IN ('user','assistant') ORDER BY m.seq LIMIT ?",
            (since, limit),
        )

    def trouble_since(self, since: float, limit: int = 20) -> list[dict[str, Any]]:
        """Turns that failed (error notices) or were cut off (interrupted replies) since ``since``."""
        return self.query(
            "SELECT m.*, c.title AS conversation_title FROM messages m JOIN conversations c ON c.id=m.conversation_id "
            "WHERE m.created_at>? AND (m.kind='error' OR (m.status='interrupted' AND m.role='assistant')) "
            "ORDER BY m.seq DESC LIMIT ?",
            (since, limit),
        )

    # -- memories ---------------------------------------------------------
    def add_memory(self, text: str, kind: str = "fact", importance: int = 3, source: str = "",
                   pinned: bool = False, meta: dict | None = None) -> dict[str, Any]:
        now = time.time()
        mem = {
            "id": new_id("mem"),
            "text": text.strip(),
            "kind": kind,
            "importance": max(1, min(5, int(importance))),
            "source": source,
            "pinned": 1 if pinned else 0,
            "created_at": now,
            "updated_at": now,
            "meta": meta or {},
        }
        self._insert("memories", mem)
        return self.get_memory(mem["id"])  # type: ignore[return-value]

    def get_memory(self, memory_id: str) -> dict[str, Any] | None:
        return self.query_one("SELECT * FROM memories WHERE id=?", (memory_id,))

    def update_memory(self, memory_id: str, **values: Any) -> dict[str, Any] | None:
        if "importance" in values:
            values["importance"] = max(1, min(5, int(values["importance"])))
        values.setdefault("updated_at", time.time())
        self._update("memories", "id", memory_id, values)
        if "text" in values:
            self.execute("DELETE FROM memory_vectors WHERE memory_id=?", (memory_id,))  # re-embedded later
        return self.get_memory(memory_id)

    def delete_memory(self, memory_id: str) -> bool:
        self.execute("DELETE FROM memory_vectors WHERE memory_id=?", (memory_id,))
        cur = self.execute("DELETE FROM memories WHERE id=?", (memory_id,))
        return cur.rowcount > 0

    def list_memories(self, kind: str | None = None, status: str = "active", limit: int = 500) -> list[dict[str, Any]]:
        if kind:
            return self.query(
                "SELECT * FROM memories WHERE status=? AND kind=? ORDER BY pinned DESC, importance DESC, updated_at DESC LIMIT ?",
                (status, kind, limit),
            )
        return self.query(
            "SELECT * FROM memories WHERE status=? ORDER BY pinned DESC, importance DESC, updated_at DESC LIMIT ?",
            (status, limit),
        )

    def search_memories(self, text: str, limit: int = 8, kinds: Iterable[str] | None = None) -> list[dict[str, Any]]:
        query = fts_query(text)
        if not query:
            return []
        kind_list = list(kinds or [])
        kind_sql = f" AND m.kind IN ({', '.join('?' for _ in kind_list)})" if kind_list else ""
        try:
            rows = self.query(
                "SELECT m.*, bm25(memories_fts) AS rank FROM memories_fts JOIN memories m ON m.rowid = memories_fts.rowid "
                f"WHERE memories_fts MATCH ? AND m.status='active'{kind_sql} ORDER BY rank LIMIT ?",
                (query, *kind_list, limit * 4),
            )
        except sqlite3.OperationalError:
            return []
        now = time.time()
        for row in rows:
            age_days = max(0.0, (now - (row.get("last_used_at") or row["updated_at"])) / 86400)
            relevance = -float(row.get("rank") or 0.0)  # bm25: lower is better
            row["score"] = relevance + 0.35 * row["importance"] + (1.5 if row["pinned"] else 0) - min(age_days, 90) * 0.01
        rows.sort(key=lambda r: r["score"], reverse=True)
        return rows[:limit]

    def find_similar_memory(self, text: str) -> dict[str, Any] | None:
        """Return an existing memory that is nearly the same statement, if any."""
        normalized = _normalize(text)
        if not normalized:
            return None
        for row in self.search_memories(text, limit=6):
            other = _normalize(row["text"])
            if other == normalized or _jaccard(normalized, other) >= 0.8:
                return row
        return None

    def touch_memories(self, ids: Iterable[str]) -> None:
        now = time.time()
        for memory_id in ids:
            self.execute(
                "UPDATE memories SET use_count=use_count+1, last_used_at=? WHERE id=?", (now, memory_id)
            )

    # -- tasks (parallel agents) -------------------------------------------
    def create_task(self, title: str, prompt: str, kind: str = "agent", cwd: str | None = None,
                    conversation_id: str | None = None, meta: dict | None = None) -> dict[str, Any]:
        task = {
            "id": new_id("t"),
            "title": title,
            "prompt": prompt,
            "kind": kind,
            "cwd": cwd,
            "conversation_id": conversation_id,
            "created_at": time.time(),
            "meta": meta or {},
        }
        self._insert("tasks", task)
        return self.get_task(task["id"])  # type: ignore[return-value]

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        return self.query_one("SELECT * FROM tasks WHERE id=?", (task_id,))

    def update_task(self, task_id: str, **values: Any) -> dict[str, Any] | None:
        self._update("tasks", "id", task_id, values)
        return self.get_task(task_id)

    def list_tasks(self, limit: int = 100, statuses: Iterable[str] | None = None) -> list[dict[str, Any]]:
        status_list = list(statuses or [])
        if status_list:
            return self.query(
                f"SELECT * FROM tasks WHERE status IN ({', '.join('?' for _ in status_list)}) ORDER BY created_at DESC LIMIT ?",
                (*status_list, limit),
            )
        return self.query("SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,))

    def add_task_event(self, task_id: str, kind: str, content: str = "", data: dict | None = None) -> dict[str, Any]:
        event = {"task_id": task_id, "kind": kind, "content": content, "data": data or {}, "created_at": time.time()}
        cur = self.execute(
            "INSERT INTO task_events(task_id, kind, content, data, created_at) VALUES (?,?,?,?,?)",
            (task_id, kind, content, _dumps(data), event["created_at"]),
        )
        event["seq"] = cur.lastrowid
        return event

    def list_task_events(self, task_id: str, limit: int = 400) -> list[dict[str, Any]]:
        rows = self.query(
            "SELECT * FROM task_events WHERE task_id=? ORDER BY seq DESC LIMIT ?", (task_id, limit)
        )
        rows.reverse()
        return rows

    # -- reminders --------------------------------------------------------
    def add_reminder(self, text: str, due_at: float, recurrence: str = "", conversation_id: str | None = None,
                     action: str = "notify", meta: dict | None = None) -> dict[str, Any]:
        reminder = {
            "id": new_id("r"),
            "text": text,
            "due_at": due_at,
            "recurrence": recurrence,
            "conversation_id": conversation_id,
            "action": action,
            "created_at": time.time(),
            "meta": meta or {},
        }
        self._insert("reminders", reminder)
        return self.get_reminder(reminder["id"])  # type: ignore[return-value]

    def get_reminder(self, reminder_id: str) -> dict[str, Any] | None:
        return self.query_one("SELECT * FROM reminders WHERE id=?", (reminder_id,))

    def update_reminder(self, reminder_id: str, **values: Any) -> dict[str, Any] | None:
        self._update("reminders", "id", reminder_id, values)
        return self.get_reminder(reminder_id)

    def list_reminders(self, include_done: bool = False, limit: int = 200) -> list[dict[str, Any]]:
        if include_done:
            return self.query("SELECT * FROM reminders ORDER BY due_at DESC LIMIT ?", (limit,))
        return self.query("SELECT * FROM reminders WHERE status='pending' ORDER BY due_at LIMIT ?", (limit,))

    def due_reminders(self, now: float) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM reminders WHERE status='pending' AND due_at<=? ORDER BY due_at", (now,))

    def queue_routine(self, reminder: dict[str, Any], conversation_id: str, data: dict) -> dict[str, Any] | None:
        """Persist before advancing the schedule; only the first tick owns this occurrence."""
        occurrence_id = new_id("ro")
        inserted = self.execute(
            "INSERT OR IGNORE INTO routine_occurrences (id, reminder_id, due_at, conversation_id, created_at, data) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (occurrence_id, reminder["id"], reminder["due_at"], conversation_id, time.time(), _dumps(data)),
        )
        return self.get_routine(occurrence_id) if inserted.rowcount else None

    def get_routine(self, occurrence_id: str) -> dict[str, Any] | None:
        return self.query_one("SELECT * FROM routine_occurrences WHERE id=?", (occurrence_id,))

    def update_routine(self, occurrence_id: str, **values: Any) -> dict[str, Any] | None:
        self._update("routine_occurrences", "id", occurrence_id, values)
        return self.get_routine(occurrence_id)

    def pending_routines(self, now: float, max_attempts: int) -> list[dict[str, Any]]:
        return self.query(
            "SELECT * FROM routine_occurrences WHERE status IN ('queued','failed') "
            "AND attempts<? AND next_attempt_at<=? ORDER BY created_at, rowid", (max_attempts, now),
        )

    # -- evolution proposals ----------------------------------------------
    def add_proposal(self, title: str, description: str, rationale: str = "", source: str = "user",
                     meta: dict | None = None) -> dict[str, Any]:
        now = time.time()
        proposal = {
            "id": new_id("p"),
            "title": title,
            "description": description,
            "rationale": rationale,
            "source": source,
            "created_at": now,
            "updated_at": now,
            "meta": meta or {},
        }
        self._insert("proposals", proposal)
        stored = self.get_proposal(proposal["id"])
        self.sync_proposal_message(stored)
        return stored  # type: ignore[return-value]

    def get_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        return self.query_one("SELECT * FROM proposals WHERE id=?", (proposal_id,))

    def update_proposal(self, proposal_id: str, **values: Any) -> dict[str, Any] | None:
        values.setdefault("updated_at", time.time())
        self._update("proposals", "id", proposal_id, values)
        proposal = self.get_proposal(proposal_id)
        self.sync_proposal_message(proposal)
        return proposal

    def sync_proposal_message(self, proposal: dict | None) -> dict[str, Any] | None:
        from .evolution.status import chat_status

        if not proposal:
            return None
        conversation_id = (proposal.get("meta") or {}).get("conversation_id")
        if not conversation_id or not self.get_conversation(conversation_id):
            return None  # Never recreate a deleted chat.
        message_id = f"evolution:{proposal['id']}"
        data = chat_status(proposal)
        existing = self.get_message(message_id)
        if existing and existing["data"] == data:
            return existing
        return self.upsert_message(message_id, conversation_id, "assistant", "evolution_status",
                                   data["status_text"], data=data)

    def list_proposal_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        for proposal in self.query("SELECT * FROM proposals WHERE json_extract(meta, '$.conversation_id')=?",
                                   (conversation_id,)):
            self.sync_proposal_message(proposal)
        return self.query("SELECT * FROM messages WHERE conversation_id=? AND kind='evolution_status' ORDER BY seq",
                          (conversation_id,))

    def list_proposals(self, limit: int = 100, statuses: Iterable[str] | None = None) -> list[dict[str, Any]]:
        status_list = list(statuses or [])
        if status_list:
            return self.query(
                f"SELECT * FROM proposals WHERE status IN ({', '.join('?' for _ in status_list)}) ORDER BY created_at DESC LIMIT ?",
                (*status_list, limit),
            )
        return self.query("SELECT * FROM proposals ORDER BY created_at DESC LIMIT ?", (limit,))

    # -- behavior eval cases ----------------------------------------------
    def add_eval_case(self, title: str, prompt: str, rubric: str, source: str = "user",
                      data: dict | None = None) -> dict[str, Any]:
        now = time.time()
        case = {"id": new_id("ev"), "title": title, "prompt": prompt, "rubric": rubric, "source": source,
                "created_at": now, "updated_at": now, "data": data or {}, "meta": {}}
        self._insert("eval_cases", case)
        return self.get_eval_case(case["id"])  # type: ignore[return-value]

    def get_eval_case(self, case_id: str) -> dict[str, Any] | None:
        return self.query_one("SELECT * FROM eval_cases WHERE id=?", (case_id,))

    def update_eval_case(self, case_id: str, **values: Any) -> dict[str, Any] | None:
        values.setdefault("updated_at", time.time())
        self._update("eval_cases", "id", case_id, values)
        return self.get_eval_case(case_id)

    def list_eval_cases(self, status: str | None = "active", limit: int = 200) -> list[dict[str, Any]]:
        if status:
            return self.query("SELECT * FROM eval_cases WHERE status=? ORDER BY created_at DESC LIMIT ?", (status, limit))
        return self.query("SELECT * FROM eval_cases ORDER BY created_at DESC LIMIT ?", (limit,))

    def delete_eval_case(self, case_id: str) -> bool:
        return self.execute("DELETE FROM eval_cases WHERE id=?", (case_id,)).rowcount > 0

    # -- memory vectors ---------------------------------------------------
    def set_memory_vector(self, memory_id: str, model: str, text: str, text_hash: str, vector: bytes) -> bool:
        """Store the embedding of ``text``, but only if the memory still says exactly that (it can be edited or
        forgotten while the embedding is computed). Returns whether it was stored."""
        cur = self.execute(
            "INSERT INTO memory_vectors(memory_id, model, text_hash, vector, updated_at) "
            "SELECT ?,?,?,?,? WHERE EXISTS (SELECT 1 FROM memories WHERE id=? AND text=?) "
            "ON CONFLICT(memory_id) DO UPDATE SET model=excluded.model, text_hash=excluded.text_hash, "
            "vector=excluded.vector, updated_at=excluded.updated_at",
            (memory_id, model, text_hash, vector, time.time(), memory_id, text),
        )
        return cur.rowcount > 0

    def memory_vectors(self, model: str) -> list[dict[str, Any]]:
        """Vectors of active memories embedded with ``model``."""
        return self.query(
            "SELECT v.memory_id, v.text_hash, v.vector FROM memory_vectors v JOIN memories m ON m.id = v.memory_id "
            "WHERE v.model=? AND m.status='active'", (model,))

    def memories_needing_vectors(self, model: str, limit: int = 256) -> list[dict[str, Any]]:
        return self.query(
            "SELECT m.id, m.text FROM memories m LEFT JOIN memory_vectors v ON v.memory_id = m.id AND v.model=? "
            "WHERE m.status='active' AND (v.memory_id IS NULL) ORDER BY m.updated_at DESC LIMIT ?", (model, limit))

    # -- journal & notifications ------------------------------------------
    def journal(self, kind: str, title: str, detail: str = "", data: dict | None = None) -> dict[str, Any]:
        entry = {"kind": kind, "title": title, "detail": detail, "data": data or {}, "created_at": time.time()}
        cur = self.execute(
            "INSERT INTO journal(kind, title, detail, data, created_at) VALUES (?,?,?,?,?)",
            (kind, title, detail, _dumps(data), entry["created_at"]),
        )
        entry["seq"] = cur.lastrowid
        return entry

    def list_journal(self, limit: int = 100, kinds: Iterable[str] | None = None) -> list[dict[str, Any]]:
        kind_list = list(kinds or [])
        if kind_list:
            return self.query(
                f"SELECT * FROM journal WHERE kind IN ({', '.join('?' for _ in kind_list)}) ORDER BY seq DESC LIMIT ?",
                (*kind_list, limit),
            )
        return self.query("SELECT * FROM journal ORDER BY seq DESC LIMIT ?", (limit,))

    def add_notification(self, kind: str, title: str, body: str = "", data: dict | None = None) -> dict[str, Any]:
        note = {
            "id": new_id("n"), "kind": kind, "title": title, "body": body, "data": data or {}, "created_at": time.time(),
        }
        self._insert("notifications", note)
        return self.query_one("SELECT * FROM notifications WHERE id=?", (note["id"],))  # type: ignore[return-value]

    def list_notifications(self, limit: int | None = 50, unread_only: bool = False) -> list[dict[str, Any]]:
        where = "WHERE read=0" if unread_only else ""
        return self.query(f"SELECT * FROM notifications {where} ORDER BY seq DESC LIMIT ?", (-1 if limit is None else limit,))

    def read_notification_ids(self) -> list[str]:
        return [note["id"] for note in self.query("SELECT id FROM notifications WHERE read=1")]

    def mark_notifications_read(self, ids: Iterable[str] | None = None) -> None:
        id_list = list(ids or [])
        if id_list:
            self.execute(
                f"UPDATE notifications SET read=1 WHERE id IN ({', '.join('?' for _ in id_list)})", id_list
            )
        elif ids is None:
            self.execute("UPDATE notifications SET read=1 WHERE read=0")


def _normalize(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def _jaccard(a: str, b: str) -> float:
    sa, sb = set(a.split()), set(b.split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)
