"""Forward-only v4 migration; imported only by Store._migrate."""

FACTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS facts_events (
    id TEXT PRIMARY KEY, source TEXT NOT NULL, external_id TEXT NOT NULL,
    title TEXT NOT NULL, start REAL NOT NULL, end REAL NOT NULL, tz TEXT NOT NULL,
    location TEXT NOT NULL DEFAULT '', attendees TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'confirmed', version TEXT NOT NULL DEFAULT '',
    fetched_at REAL NOT NULL, raw TEXT NOT NULL DEFAULT '{}',
    UNIQUE(source, external_id), CHECK(end >= start)
);
CREATE INDEX IF NOT EXISTS idx_facts_events_time ON facts_events(start, end);
CREATE TABLE IF NOT EXISTS facts_threads (
    id TEXT PRIMARY KEY, source TEXT NOT NULL, external_id TEXT NOT NULL,
    subject TEXT NOT NULL, participants TEXT NOT NULL DEFAULT '[]',
    last_message_at REAL NOT NULL, needs_reply INTEGER NOT NULL DEFAULT 0,
    summary TEXT NOT NULL DEFAULT '', fetched_at REAL NOT NULL,
    UNIQUE(source, external_id)
);
CREATE TABLE IF NOT EXISTS facts_commitments (
    id TEXT PRIMARY KEY, text TEXT NOT NULL, owner TEXT NOT NULL,
    due REAL, status TEXT NOT NULL DEFAULT 'open', source_ref TEXT NOT NULL,
    created_at REAL NOT NULL,
    CHECK(owner IN ('user', 'assistant')), CHECK(status IN ('open', 'done', 'dropped'))
);
CREATE INDEX IF NOT EXISTS idx_facts_commitments_due ON facts_commitments(status, due);
CREATE TABLE IF NOT EXISTS facts_people (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, aliases TEXT NOT NULL DEFAULT '[]',
    emails TEXT NOT NULL DEFAULT '[]', notes_ref TEXT NOT NULL DEFAULT '', tz TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS facts_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, entity TEXT NOT NULL, entity_id TEXT NOT NULL,
    change_kind TEXT NOT NULL, before_json TEXT, after_json TEXT NOT NULL,
    detected_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_facts_changes_entity ON facts_changes(entity, entity_id, id);
CREATE TABLE IF NOT EXISTS facts_provenance (
    entity TEXT NOT NULL, entity_id TEXT NOT NULL, source TEXT NOT NULL,
    fetched_at REAL NOT NULL, ttl_seconds REAL NOT NULL CHECK(ttl_seconds > 0),
    PRIMARY KEY(entity, entity_id)
);
CREATE TABLE IF NOT EXISTS facts_sources (
    id TEXT PRIMARY KEY, entity TEXT NOT NULL, cursor TEXT,
    last_attempt_at REAL, last_success_at REAL, error TEXT NOT NULL DEFAULT ''
);
-- Materialized artifacts are invalidated in the same transaction as a fact.
-- Future Ideas/Day Sheet rows can share this index without scanning model text.
CREATE TABLE IF NOT EXISTS facts_artifacts (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, data TEXT NOT NULL DEFAULT '{}',
    stale INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS facts_dependencies (
    artifact_id TEXT NOT NULL REFERENCES facts_artifacts(id) ON DELETE CASCADE,
    entity TEXT NOT NULL, entity_id TEXT NOT NULL,
    PRIMARY KEY(artifact_id, entity, entity_id)
);
CREATE INDEX IF NOT EXISTS idx_facts_dependencies ON facts_dependencies(entity, entity_id);
"""
