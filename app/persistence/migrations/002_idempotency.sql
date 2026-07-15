-- Idempotency-Key -> run mapping (PRD §3.3). No FK to `runs`: the key is
-- reserved (M6.T3's UNIQUE-constraint race) before the run it points at is
-- necessarily persisted — see SqliteRepository.reserve_idempotency_key.
CREATE TABLE idempotency_keys (
    key             TEXT PRIMARY KEY,
    request_hash    TEXT NOT NULL,
    run_id          TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
