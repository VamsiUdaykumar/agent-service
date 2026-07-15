-- Envelope projection: current state of a run, eagerly folded from `events`.
CREATE TABLE runs (
    id                  TEXT PRIMARY KEY,
    status              TEXT NOT NULL,
    agent_id            TEXT NOT NULL,
    seed                INTEGER NOT NULL,
    input               TEXT NOT NULL,
    metadata            TEXT,
    tokens_in           INTEGER NOT NULL DEFAULT 0,
    tokens_out          INTEGER NOT NULL DEFAULT 0,
    cost_usd            REAL NOT NULL DEFAULT 0,
    trace_id            TEXT NOT NULL,
    error_code          TEXT,
    error_message       TEXT,
    error_retryable     INTEGER,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX idx_runs_created_at ON runs (created_at);
CREATE INDEX idx_runs_status ON runs (status);
CREATE INDEX idx_runs_agent_id ON runs (agent_id);

-- Per-step projection: current state of each step, eagerly folded from `events`.
CREATE TABLE steps (
    run_id                  TEXT NOT NULL REFERENCES runs (id),
    step_id                 TEXT NOT NULL,
    parent_step_id          TEXT,
    step_type               TEXT NOT NULL,
    status                  TEXT NOT NULL,
    attempt                 INTEGER NOT NULL DEFAULT 1,
    tokens_in               INTEGER NOT NULL DEFAULT 0,
    tokens_out              INTEGER NOT NULL DEFAULT 0,
    cost_usd                REAL NOT NULL DEFAULT 0,
    last_error_code         TEXT,
    last_error_message      TEXT,
    last_error_retryable    INTEGER,
    created_at               TEXT NOT NULL,
    updated_at               TEXT NOT NULL,
    PRIMARY KEY (run_id, step_id)
);

CREATE INDEX idx_steps_run_id ON steps (run_id);

-- Append-only event log: the product's source of truth.
CREATE TABLE events (
    run_id          TEXT NOT NULL REFERENCES runs (id),
    sequence        INTEGER NOT NULL,
    event_type      TEXT NOT NULL,
    payload_json    TEXT NOT NULL,
    occurred_at     TEXT NOT NULL,
    PRIMARY KEY (run_id, sequence)
);

-- `schema_migrations` itself is bootstrapped by the migration runner
-- (app/persistence/migrations.py) before any file here is applied.
