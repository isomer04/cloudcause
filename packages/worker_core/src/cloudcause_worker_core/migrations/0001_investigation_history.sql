-- Investigation history. PostgreSQL DDL, applied in filename order.
--
-- Timestamps are ISO-8601 UTC text, which sorts correctly and round-trips through
-- the Pydantic contracts without a driver-specific type.
-- state_json and event_json hold the versioned contract payloads; the columns
-- beside them exist for listing, filtering, and retention only.

CREATE TABLE IF NOT EXISTS investigations (
    investigation_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    stage TEXT NOT NULL,
    question TEXT NOT NULL,
    scenario_id TEXT NOT NULL,
    providers TEXT NOT NULL,
    data_mode TEXT NOT NULL,
    agent_mode TEXT NOT NULL,
    has_report INTEGER NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    state_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS investigations_created_at_idx ON investigations (created_at);

CREATE TABLE IF NOT EXISTS investigation_events (
    investigation_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    at TEXT NOT NULL,
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    provider TEXT,
    message TEXT NOT NULL,
    event_json TEXT NOT NULL,
    PRIMARY KEY (investigation_id, sequence)
);
