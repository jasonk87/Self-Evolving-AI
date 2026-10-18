"""Forward-only v5 migration (Weebo 3 team, policy, connectors, ideas and growth); imported only by Store._migrate.

Fields that grow over time live in each row's ``data`` JSON column, which the Store decodes on read."""

TEAM_SCHEMA = """
CREATE TABLE IF NOT EXISTS bots (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'proposed',
    conversation_id TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL, data TEXT NOT NULL DEFAULT '{}',
    CHECK(status IN ('proposed', 'active', 'paused', 'archived', 'rejected'))
);
CREATE INDEX IF NOT EXISTS idx_bots_status ON bots(status);
CREATE TABLE IF NOT EXISTS bot_jobs (
    id TEXT PRIMARY KEY, bot_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
    created_at REAL NOT NULL, updated_at REAL NOT NULL, data TEXT NOT NULL DEFAULT '{}',
    CHECK(status IN ('open', 'running', 'done', 'failed', 'cancelled'))
);
CREATE INDEX IF NOT EXISTS idx_bot_jobs_bot ON bot_jobs(bot_id, status);
CREATE TABLE IF NOT EXISTS bot_changes (
    id TEXT PRIMARY KEY, bot_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'requested',
    created_at REAL NOT NULL, updated_at REAL NOT NULL, data TEXT NOT NULL DEFAULT '{}',
    CHECK(status IN ('requested', 'proposed', 'applied', 'rejected'))
);
CREATE TABLE IF NOT EXISTS bot_usage (
    bot_id TEXT NOT NULL, day TEXT NOT NULL, turns INTEGER NOT NULL DEFAULT 0, failures INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(bot_id, day)
);
CREATE TABLE IF NOT EXISTS standing_rules (
    id TEXT PRIMARY KEY, scope TEXT NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL DEFAULT '',
    effect TEXT NOT NULL DEFAULT 'allow', created_at REAL NOT NULL, data TEXT NOT NULL DEFAULT '{}',
    CHECK(effect IN ('allow', 'deny'))
);
CREATE TABLE IF NOT EXISTS report_rules (
    id TEXT PRIMARY KEY, scope TEXT NOT NULL, match TEXT NOT NULL, bucket TEXT NOT NULL,
    created_at REAL NOT NULL, data TEXT NOT NULL DEFAULT '{}',
    CHECK(bucket IN ('interrupt', 'brief', 'log'))
);
CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY, bot_id TEXT NOT NULL, job_id TEXT, bucket TEXT NOT NULL,
    created_at REAL NOT NULL, delivered_at REAL, data TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_reports_bucket ON reports(bucket, delivered_at);
CREATE TABLE IF NOT EXISTS needs_you (
    id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'open', created_at REAL NOT NULL, updated_at REAL NOT NULL,
    data TEXT NOT NULL DEFAULT '{}',
    CHECK(status IN ('open', 'approved', 'denied', 'expired'))
);
CREATE TABLE IF NOT EXISTS connectors (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'registered',
    created_at REAL NOT NULL, updated_at REAL NOT NULL, data TEXT NOT NULL DEFAULT '{}',
    CHECK(kind IN ('app', 'mcp')),
    CHECK(status IN ('registered', 'testing', 'tested', 'failed', 'approved', 'disabled'))
);
CREATE TABLE IF NOT EXISTS ideas (
    id TEXT PRIMARY KEY, key TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'new',
    created_at REAL NOT NULL, updated_at REAL NOT NULL, expires_at REAL, data TEXT NOT NULL DEFAULT '{}',
    CHECK(status IN ('new', 'discussed', 'done', 'dismissed', 'expired'))
);
CREATE INDEX IF NOT EXISTS idx_ideas_status ON ideas(status, created_at);
CREATE TABLE IF NOT EXISTS workflows (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'proposed',
    created_at REAL NOT NULL, updated_at REAL NOT NULL, data TEXT NOT NULL DEFAULT '{}',
    CHECK(status IN ('proposed', 'active', 'paused', 'retired', 'rejected'))
);
CREATE TABLE IF NOT EXISTS capability_gaps (
    id TEXT PRIMARY KEY, key TEXT NOT NULL UNIQUE, count INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'open', created_at REAL NOT NULL, updated_at REAL NOT NULL,
    data TEXT NOT NULL DEFAULT '{}',
    CHECK(status IN ('open', 'proposed', 'resolved', 'dismissed'))
);
"""
