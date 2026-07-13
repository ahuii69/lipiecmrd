-- Turn execution / effects / outbox / session locks
-- Applied via ensure_turn_schema() + ensure_lock_schema() on init_db.
-- Kept as reference DDL for Postgres bootstrap alignment.

CREATE TABLE IF NOT EXISTS turn_executions (
    turn_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    request_id TEXT NOT NULL DEFAULT '',
    correlation_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    result_json TEXT,
    error_json TEXT,
    started_ts DOUBLE PRECISION,
    completed_ts DOUBLE PRECISION,
    created_ts DOUBLE PRECISION NOT NULL,
    updated_ts DOUBLE PRECISION NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_turn_exec_user_session_ts
    ON turn_executions(user_id, session_id, created_ts DESC);

CREATE TABLE IF NOT EXISTS turn_effects (
    id TEXT PRIMARY KEY,
    turn_id TEXT NOT NULL,
    effect_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    retry_count INTEGER NOT NULL DEFAULT 0,
    result_json TEXT,
    last_error TEXT,
    created_ts DOUBLE PRECISION NOT NULL,
    updated_ts DOUBLE PRECISION NOT NULL,
    completed_ts DOUBLE PRECISION,
    UNIQUE(turn_id, effect_type)
);

CREATE TABLE IF NOT EXISTS turn_outbox (
    id TEXT PRIMARY KEY,
    turn_id TEXT NOT NULL,
    effect_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    retry_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    available_at DOUBLE PRECISION NOT NULL DEFAULT 0,
    created_ts DOUBLE PRECISION NOT NULL,
    updated_ts DOUBLE PRECISION NOT NULL,
    UNIQUE(turn_id, effect_type)
);

CREATE TABLE IF NOT EXISTS turn_session_locks (
    lock_key TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    turn_id TEXT NOT NULL DEFAULT '',
    acquired_ts DOUBLE PRECISION NOT NULL,
    expires_ts DOUBLE PRECISION NOT NULL
);
