# Implementation Plan — Agent Runs API & Observability

Derived from `docs/specs.md`. Organized into **Milestones** (mapped to PRD §7 phases), each containing **Tasks**, each containing **Subtasks**. Every item is a checklist entry with a numeric ID and an explicit dependency list — nothing is an orphan; everything traces back to Milestone 0.

## Legend

- ID format: `M<milestone>.T<task>` for tasks, `M<milestone>.T<task>.<subtask>` for subtasks.
- **Depends on:** lists IDs that must be `done` before starting this item. `—` only appears on the very first item in the plan.
- Each task's context is enough to start implementing without re-reading the PRD, but references the relevant PRD section (`§x.y`) for full rationale.

## Amendments (approved 2026-07-14)

1. **`cancelling` is a persisted status transition**, written through the same repository `append_event` path as every other status change — not an in-memory-only flag. An in-memory signal may still be used to interrupt the running `asyncio.Task` promptly, but it is never the source of truth; a crashed/restarted process must be able to observe `cancelling` from the store alone. Reflected in M1.T1, M4.T3.3, M4.T8, M5.T5, M5.T6.
2. **`trace_id` is generated once at run creation** (new utility M0.T9, used in M4.T3.1) and persisted immediately as part of the `pending` row — not read back from a span later. The OTel root span is started *with* that pre-generated ID (M7.T2.1) so the envelope and the observability backend always agree. This also keeps faith with the cross-cutting invariant that "the API never reads from OTel" (PRD §2). Reflected in M0.T9, M2.T1.1, M4.T1.2, M4.T3, M7.T2, M7.T3.4.
3. **Event `sequence` numbers are 1-indexed** — the first event of every run is sequence `1`. This leaves `0` (or an absent header) as a natural, un-special-cased sentinel for "replay from the beginning" in `Last-Event-ID` handling. Reflected in M1.T3, M2.T3.2, M5.T1, M5.T2.

---

## Milestone 0 — Scaffold & Environment

Goal: an empty-but-runnable skeleton. *Done when:* `docker compose up` serves `/docs` (PRD §7, Phase 0).

### Task M0.T1 — Repository & package layout
- [ ] **M0.T1** Create the layered package skeleton so layer separation is structurally enforced (PRD §4): `app/api/`, `app/domain/`, `app/services/`, `app/persistence/`, `app/runner/`, `app/telemetry/`, each with `__init__.py`, plus `tests/` mirroring the same structure. Add `.gitignore` (Python, venv, `.env`, `*.db`, `__pycache__`).
  **Depends on:** —
  - [ ] M0.T1.1 Create the six `app/*` packages with empty `__init__.py` files.
        **Depends on:** M0.T1
  - [ ] M0.T1.2 Create `tests/api/`, `tests/domain/`, `tests/services/`, `tests/persistence/`, `tests/runner/`, `tests/telemetry/` with `__init__.py` / `conftest.py` placeholders.
        **Depends on:** M0.T1.1
  - [ ] M0.T1.3 Add `.gitignore` and an empty `.env.example`.
        **Depends on:** M0.T1.1

### Task M0.T2 — Dependency & tooling setup
- [ ] **M0.T2** Add `pyproject.toml` pinning Python 3.12+, FastAPI, Pydantic v2, `pydantic-settings`, `aiosqlite`, `python-ulid`, `opentelemetry-sdk` + OTLP exporter packages, and dev deps `pytest`, `pytest-asyncio`, `httpx`, `ruff`, `mypy` (PRD §6). This is the single source of truth for what the project depends on.
  **Depends on:** M0.T1
  - [ ] M0.T2.1 Write `pyproject.toml` with runtime dependencies and Python version constraint.
        **Depends on:** M0.T1
  - [ ] M0.T2.2 Add dev-dependency group (test/lint/type tools) and `[tool.ruff]` + `[tool.mypy]` sections (strict mode, `app/domain` and `app/persistence` type-checked first).
        **Depends on:** M0.T2.1
  - [ ] M0.T2.3 Verify a clean install works (`pip install -e .[dev]` or `uv sync`) in a fresh virtualenv.
        **Depends on:** M0.T2.2

### Task M0.T3 — Configuration module
- [ ] **M0.T3** Implement `app/config.py` using `pydantic-settings` for all env-driven knobs: SQLite path, `SIM_SPEED`, OTLP endpoint, service name/version, idempotency-key TTL. One `Settings` object, loaded once, injected — no scattered `os.environ` reads (PRD §6).
  **Depends on:** M0.T2
  - [ ] M0.T3.1 Define the `Settings` model with typed fields and sane local defaults (in-memory-friendly SQLite path, `SIM_SPEED=1`, debug OTLP endpoint).
        **Depends on:** M0.T2
  - [ ] M0.T3.2 Populate `.env.example` with every field `Settings` reads, each with a comment.
        **Depends on:** M0.T3.1

### Task M0.T4 — FastAPI app skeleton
- [ ] **M0.T4** Create `app/main.py` with an app-factory function, empty router includes (to be filled in Milestone 4), and a `GET /health` liveness endpoint. This is the process entrypoint used by both `uvicorn` locally and the Docker image.
  **Depends on:** M0.T3
  - [ ] M0.T4.1 Write `create_app()` factory reading `Settings`, registering (currently empty) routers.
        **Depends on:** M0.T3
  - [ ] M0.T4.2 Add `GET /health` returning `{"status": "ok"}`.
        **Depends on:** M0.T4.1
  - [ ] M0.T4.3 Confirm `uvicorn app.main:app --reload` boots and `/docs` renders (empty Swagger UI is fine at this stage).
        **Depends on:** M0.T4.2

### Task M0.T5 — API Dockerfile
- [ ] **M0.T5** Write a multi-stage `Dockerfile` for the API service (build deps → slim runtime), running `uvicorn app.main:app`.
  **Depends on:** M0.T4
  - [ ] M0.T5.1 Write the Dockerfile.
        **Depends on:** M0.T4
  - [ ] M0.T5.2 Build locally and confirm the container serves `/health`.
        **Depends on:** M0.T5.1

### Task M0.T6 — OTel Collector config with debug fallback
- [ ] **M0.T6** Add `otel-collector-config.yaml` with an OTLP receiver and two exporters: `debug` (always on, so the system runs with zero accounts) and `otlphttp` targeting Grafana Cloud, gated by env vars that are empty by default (PRD §4, §6). This file is the seam for sampling/second exporters later.
  **Depends on:** M0.T1
  - [ ] M0.T6.1 Write the receiver + `debug` exporter pipeline (traces + metrics).
        **Depends on:** M0.T1
  - [ ] M0.T6.2 Add the `otlphttp` exporter block reading Grafana Cloud creds from env vars, wired into the same pipeline behind a no-op default.
        **Depends on:** M0.T6.1

### Task M0.T7 — Docker Compose wiring
- [ ] **M0.T7** Write `docker-compose.yml` with `api` and `collector` services, shared network, volume for SQLite persistence, and env passthrough from `.env`.
  **Depends on:** M0.T5, M0.T6
  - [ ] M0.T7.1 Define both services, port mappings (`8000:8000` for API), and the SQLite volume.
        **Depends on:** M0.T5, M0.T6
  - [ ] M0.T7.2 Wire `OTEL_EXPORTER_OTLP_ENDPOINT` on the `api` service to point at `collector:4317`.
        **Depends on:** M0.T7.1
  - [ ] M0.T7.3 Run `docker compose up` end to end and confirm `/docs` is reachable from the host.
        **Depends on:** M0.T7.2

### Task M0.T8 — Makefile & developer ergonomics
- [ ] **M0.T8** Add a `Makefile` with `demo`, `test`, `lint`, `typecheck`, `up`, `down` targets, matching PRD §6's "two-command startup" promise.
  **Depends on:** M0.T7, M0.T2
  - [ ] M0.T8.1 Add `make up`/`make down` wrapping `docker compose`.
        **Depends on:** M0.T7
  - [ ] M0.T8.2 Add `make lint` (ruff) and `make typecheck` (mypy) targets.
        **Depends on:** M0.T2
  - [ ] M0.T8.3 Add `make test` (pytest) target; `make demo` stubbed for now (filled in Milestone 8).
        **Depends on:** M0.T2

### Task M0.T9 — Trace ID pre-generation utility
- [ ] **M0.T9** Implement `app/telemetry/ids.py::generate_trace_id() -> str`: a W3C-trace-context-compatible 128-bit hex trace ID, generated independently of the full OTel SDK setup (Milestone 7) so it can be called at run-creation time in Milestone 4. This is what lets the envelope and the OTel backend always agree on `trace_id` — generated once, at creation, never read back from a span (amendment 2).
  **Depends on:** M0.T2
  - [ ] M0.T9.1 Implement the generator using `opentelemetry.sdk.trace.id_generator.RandomIdGenerator` (or equivalent) so the output is guaranteed to be a valid OTel trace ID, not just a random-looking string.
        **Depends on:** M0.T2
  - [ ] M0.T9.2 Unit-test the output format (32 lowercase hex chars, non-zero) and uniqueness across repeated calls.
        **Depends on:** M0.T9.1

---

## Milestone 1 — Domain Layer (pure, no I/O)

Goal: state machine, event types, agent profile definitions, importable with zero I/O dependencies — the checkable proof of layer separation (PRD §4, §7 Phase 1).

### Task M1.T1 — Status enums & transition rules
- [ ] **M1.T1** Define `RunStatus` (`pending, running, completed, failed, cancelled, cancelling`) and `StepStatus` enums in `app/domain/status.py`, plus a pure transition-validation function `can_transition(from, to) -> bool` encoding the legal graph from PRD §3.1, including that terminal states (`completed, failed, cancelled`) accept no further transitions. `cancelling` is a fully persisted, first-class status like any other in this graph — not an in-memory-only flag — so it is written and read through the same repository path as every other transition (amendment 1; see M5.T5).
  **Depends on:** M0.T1
  - [ ] M1.T1.1 Define the enums.
        **Depends on:** M0.T1
  - [ ] M1.T1.2 Implement `can_transition` as a static adjacency map, not an `if/elif` chain, so it's trivially auditable.
        **Depends on:** M1.T1.1
  - [ ] M1.T1.3 Implement `is_terminal(status) -> bool` used by both the transition check and the persistence layer's immutability guard (M2.T3).
        **Depends on:** M1.T1.1

### Task M1.T2 — Structured error object
- [ ] **M1.T2** Define the structured error model (`app/domain/errors.py`) attached to failed runs/steps: `{code, message, retryable}`. This is distinct from the API error envelope (M4.T2) — this one lives on the domain event/run, the API one wraps HTTP responses.
  **Depends on:** M1.T1
  - [ ] M1.T2.1 Define `RunError` dataclass/model with `code`, `message`, `retryable: bool`.
        **Depends on:** M1.T1
  - [ ] M1.T2.2 Define the closed set of domain error codes used by the runner (e.g. `step_failed`, `interrupted_by_restart`, `cancelled_by_user`).
        **Depends on:** M1.T2.1

### Task M1.T3 — Event type definitions
- [ ] **M1.T3** Define the append-only event types in `app/domain/events.py`: `RunCreated`, `RunStarted`, `StepStarted`, `StepCompleted`, `StepFailed`, `StepRetried`, `RunCompleted`, `RunFailed`, `RunCancelled`. Every event carries `run_id`, a monotonic per-run `sequence` number starting at 1 for the first event of each run (amendment 3 — `0` is reserved as the "no events seen yet" sentinel, see M5.T2), `occurred_at`, and a type-specific payload. This is the schema the SQLite event log (M2.T1) and the SSE stream (M5.T1) both serve directly.
  **Depends on:** M1.T1, M1.T2
  - [ ] M1.T3.1 Define a common `BaseEvent` with `run_id`, `sequence`, `occurred_at`, `event_type` discriminator. Sequence numbers are 1-indexed per run.
        **Depends on:** M1.T1
  - [ ] M1.T3.2 Define each concrete event subtype with its payload fields (e.g. `StepCompleted` carries `step_id`, `tokens_in/out`, `cost_usd`, `duration_ms`).
        **Depends on:** M1.T3.1, M1.T2
  - [ ] M1.T3.3 Define a discriminated union / registry so events can be (de)serialized generically by the persistence layer.
        **Depends on:** M1.T3.2

### Task M1.T4 — Agent profile definitions
- [ ] **M1.T4** Define the three agent profiles as one config dict in `app/domain/profiles.py` (PRD §4, §7 Phase 2): `researcher` (5–8 steps, 10% fail), `simple` (2–3 steps, 2% fail), `flaky` (4–6 steps, 35% fail + 5% non-retryable). Each profile specifies step-type mix (model_call, tool_call, sub_agent), step count range, failure rate, and non-retryable rate. This is data the runner (Milestone 3) consumes; it stays here because it's a pure specification, not execution logic.
  **Depends on:** M1.T1
  - [ ] M1.T4.1 Define an `AgentProfile` model (step count range, step-type weights, fail rate, non-retryable rate).
        **Depends on:** M1.T1
  - [ ] M1.T4.2 Instantiate the three named profiles from PRD §7 Phase 2 as a `PROFILES: dict[str, AgentProfile]` registry keyed by `agent_id`.
        **Depends on:** M1.T4.1

### Task M1.T5 — Domain unit tests
- [ ] **M1.T5** Unit-test the state machine and event/profile definitions with zero I/O: illegal transitions rejected, terminal states immutable, event sequence ordering enforced, all three profiles resolve to valid configs. This is the first "Done when" gate in PRD §7 Phase 1.
  **Depends on:** M1.T3, M1.T4
  - [ ] M1.T5.1 Test every legal and illegal transition pair for `can_transition`.
        **Depends on:** M1.T1
  - [ ] M1.T5.2 Test that `is_terminal` states reject all outgoing transitions.
        **Depends on:** M1.T1
  - [ ] M1.T5.3 Test event (de)serialization round-trips for every event type.
        **Depends on:** M1.T3
  - [ ] M1.T5.4 Add an import-linter / lint rule asserting `app/domain` imports nothing from `app/persistence`, `app/api`, `app/services`, or `app/runner` — the structural proof of layer separation.
        **Depends on:** M1.T3, M1.T4

---

## Milestone 2 — Persistence Layer

Goal: append-only SQLite event log with eager, atomically-updated projections. *Done when:* projection tests pass (PRD §7 Phase 1, test #2).

### Task M2.T1 — Schema & migrations
- [ ] **M2.T1** Write the SQLite schema: `runs` (envelope projection: id, status, agent_id, seed, input, metadata, tokens, cost_usd, trace_id, created_at, ...), `steps` (per-step projection: run_id, step_id, type, status, attempt, last_error, tokens, cost_usd), `events` (append-only log: run_id, sequence, event_type, payload_json, occurred_at, `UNIQUE(run_id, sequence)`). Apply via a lightweight migration runner (plain SQL files + a version table is enough — no need for Alembic at this scale).
  **Depends on:** M1.T3, M0.T2
  - [ ] M2.T1.1 Write `001_init.sql` creating `runs`, `steps`, `events` tables with the constraints above. `runs.trace_id` is `NOT NULL` — every run has a trace_id from the moment it's inserted (M4.T3.1, amendment 2), never backfilled later.
        **Depends on:** M1.T3
  - [ ] M2.T1.2 Write a tiny migration runner that applies un-applied `.sql` files in order on startup, tracked in a `schema_migrations` table.
        **Depends on:** M2.T1.1
  - [ ] M2.T1.3 Add `run_id ULID` as primary key on `runs` (time-sortable, doubles as pagination cursor per PRD §3.3).
        **Depends on:** M2.T1.1

### Task M2.T2 — Repository interface
- [ ] **M2.T2** Define `app/persistence/repository.py` as a `Protocol`/ABC: `create_run`, `append_event`, `get_run`, `list_runs`, `get_steps`, `get_events_from(run_id, after_sequence)`. This interface is the documented seam to swap SQLite for Postgres later (PRD §4) — no call site should import `aiosqlite` directly outside the implementation.
  **Depends on:** M2.T1, M1.T1
  - [ ] M2.T2.1 Define the `Repository` protocol with full type signatures and docstrings on transactional guarantees each method provides.
        **Depends on:** M2.T1

### Task M2.T3 — aiosqlite implementation
- [ ] **M2.T3** Implement `SqliteRepository` (PRD §4, §6): every `append_event` call runs in a single transaction that (a) inserts the event row, (b) updates the `runs` projection, and (c) updates the relevant `steps` row — all-or-nothing. Enforce terminal-state immutability here as a hard DB-level guard (reject writes to a run already in a terminal status), not just in the domain layer, per PRD's "Terminal run states are immutable, enforced at the store layer" invariant (§2).
  **Depends on:** M2.T2
  - [ ] M2.T3.1 Implement connection management (single `aiosqlite` connection or pool, WAL mode for single-writer concurrency).
        **Depends on:** M2.T2
  - [ ] M2.T3.2 Implement `create_run` (insert `pending` row) and `append_event` with the atomic three-part write described above. `append_event` assigns the next sequence as `COALESCE(MAX(sequence), 0) + 1`, so the first event of a run is sequence 1 (amendment 3).
        **Depends on:** M2.T3.1
  - [ ] M2.T3.3 Implement the terminal-state immutability guard: `append_event`/status-changing writes on a terminal run raise a domain-level conflict error instead of writing.
        **Depends on:** M2.T3.2, M1.T1
  - [ ] M2.T3.4 Implement `get_run`, `get_steps`, `get_events_from`.
        **Depends on:** M2.T3.2

### Task M2.T4 — ULID generation utility
- [ ] **M2.T4** Wrap `python-ulid` in `app/persistence/ids.py` so run IDs are generated in exactly one place and are guaranteed monotonic-enough for use as both identity and pagination cursor (PRD §3.3).
  **Depends on:** M0.T2
  - [ ] M2.T4.1 Implement `new_run_id() -> str`.
        **Depends on:** M0.T2

### Task M2.T5 — Cursor-paginated list query
- [ ] **M2.T5** Implement `list_runs(cursor, limit, filters)` in `SqliteRepository` using the ULID run ID directly as the cursor (`WHERE id < :cursor ORDER BY id DESC LIMIT :limit+1`), returning `{data, has_more, next_cursor}`. Support filters: `status`, `agent_id`, `metadata.<key>` (JSON column lookup), `created_after/before`. Sort is fixed at `created_at desc` per PRD §3.3 — offset pagination is explicitly rejected because pages shift under concurrent writes.
  **Depends on:** M2.T3, M2.T4
  - [ ] M2.T5.1 Implement the base cursor query and `has_more`/`next_cursor` derivation (fetch `limit+1`, trim, cursor = last row's id).
        **Depends on:** M2.T3
  - [ ] M2.T5.2 Add each filter as an optional `AND` clause; add `metadata.<key>` lookup against the JSON-stored metadata column.
        **Depends on:** M2.T5.1

### Task M2.T6 — Persistence integration tests
- [ ] **M2.T6** Integration-test the repository against a real (temp-file or `:memory:`) SQLite DB: projection correctness after a sequence of events, atomicity (a simulated failure mid-write leaves no partial state), terminal-state immutability rejection, and cursor pagination correctness under interleaved inserts. This is PRD §7 Phase 1's test #2 gate.
  **Depends on:** M2.T3, M2.T5, M1.T5
  - [ ] M2.T6.1 Test full lifecycle projection folding (`pending → running → completed`) matches expected envelope/step state after replaying events.
        **Depends on:** M2.T3
  - [ ] M2.T6.2 Test that writing to a terminal run raises and leaves the row unchanged.
        **Depends on:** M2.T3
  - [ ] M2.T6.3 Test cursor pagination returns stable, non-overlapping pages across concurrent inserts.
        **Depends on:** M2.T5

---

## Milestone 3 — Runner (fake agent)

Goal: a seeded, deterministic step generator driven purely by `(agent_id, seed, input)`. *Done when:* the determinism test passes (PRD §7 Phase 2, test #1 — "the headline claim").

### Task M3.T1 — Recipe-seeded RNG
- [ ] **M3.T1** Implement `app/runner/rng.py`: derive a deterministic `random.Random` instance from `(agent_id, seed, input)` (e.g. seed a PRNG from a hash of the recipe tuple). This is the *only* source of randomness the runner may use — no wall-clock, no `os.urandom`, per PRD §2's cross-cutting invariant.
  **Depends on:** M1.T4
  - [ ] M3.T1.1 Implement `derive_seed(agent_id, seed, input) -> int` (stable hash → int).
        **Depends on:** M1.T4
  - [ ] M3.T1.2 Implement `make_rng(agent_id, seed, input) -> random.Random`.
        **Depends on:** M3.T1.1

### Task M3.T2 — Step plan generation
- [ ] **M3.T2** Given a profile (M1.T4) and the seeded RNG (M3.T1), generate the ordered list of steps for a run: count within the profile's range, step types drawn from its weighted mix, sub-agent steps nested exactly one level deep (PRD §3.4). This is a pure function returning a step plan — no sleeping, no side effects yet.
  **Depends on:** M3.T1, M1.T4
  - [ ] M3.T2.1 Implement step-count sampling within the profile's range.
        **Depends on:** M3.T1
  - [ ] M3.T2.2 Implement step-type sampling from the profile's weighted mix, including one-level sub-agent nesting.
        **Depends on:** M3.T2.1

### Task M3.T3 — Token & cost simulation
- [ ] **M3.T3** For each `model_call` step, simulate input/output token counts from the RNG and compute cost against a static two-model price table (PRD §3.2). Keep input/output tokens as separate fields end to end — different prices, different causes. Failed attempts still consume (and record) tokens.
  **Depends on:** M3.T2
  - [ ] M3.T3.1 Define the static two-model price table (`app/runner/pricing.py`) with per-1K-token input/output rates.
        **Depends on:** M3.T2
  - [ ] M3.T3.2 Implement token sampling per `model_call` step and cost computation, applied identically whether the attempt ultimately succeeds or fails.
        **Depends on:** M3.T3.1

### Task M3.T4 — Failure, retry & sub-agent execution
- [ ] **M3.T4** Implement per-step execution: sample failure per the profile's fail rate and non-retryable rate; retryable failures re-attempt the same step (each attempt is tracked separately, feeding the "one child span per attempt" requirement in M6.T2); non-retryable failures terminate the run as `failed`. Sub-agent steps execute their nested child steps before completing.
  **Depends on:** M3.T2, M3.T3, M1.T2
  - [ ] M3.T4.1 Implement failure sampling (retryable vs non-retryable) per profile.
        **Depends on:** M3.T2
  - [ ] M3.T4.2 Implement retry loop with attempt tracking (attempt number, backoff delay derived from RNG, not wall clock).
        **Depends on:** M3.T4.1
  - [ ] M3.T4.3 Implement sub-agent step execution (recurses into a nested step plan, one level deep only).
        **Depends on:** M3.T2
  - [ ] M3.T4.4 On non-retryable failure, attach the structured `RunError` (M1.T2) and stop the plan.
        **Depends on:** M3.T4.1, M1.T2

### Task M3.T5 — SIM_SPEED-scaled latency
- [ ] **M3.T5** Simulate per-step latency from the RNG, sleep `latency / SIM_SPEED` (via `app/config.py` Settings), but *record* the unscaled simulated duration — so `SIM_SPEED=100` makes the test suite fast without corrupting recorded metrics (PRD §6).
  **Depends on:** M3.T2, M0.T3
  - [ ] M3.T5.1 Implement latency sampling per step type.
        **Depends on:** M3.T2
  - [ ] M3.T5.2 Implement the sleep-scaled / record-unscaled split, reading `SIM_SPEED` from `Settings`.
        **Depends on:** M3.T5.1, M0.T3

### Task M3.T6 — Runner execution interface
- [ ] **M3.T6** Implement `app/runner/execute.py`: an async generator/callback interface that yields domain events (M1.T3) as it executes a run's step plan — `RunStarted`, `StepStarted`, `StepCompleted`/`StepFailed`, `StepRetried`, `RunCompleted`/`RunFailed`. No HTTP, no persistence import — the service layer (Milestone 4) consumes this and persists what it yields.
  **Depends on:** M3.T3, M3.T4, M3.T5, M1.T3
  - [ ] M3.T6.1 Implement the async generator that walks the step plan and yields the correct event sequence, including retry and sub-agent events.
        **Depends on:** M3.T4, M3.T5
  - [ ] M3.T6.2 Implement a cancellation checkpoint (checked at each step boundary) that the async generator can be told to honor via an in-memory signal — the hook that Milestone 5's cancel endpoint (M5.T5.1) sets at the same time it persists the authoritative `cancelling` status (amendment 1).
        **Depends on:** M3.T6.1

### Task M3.T7 — Determinism test (headline claim)
- [ ] **M3.T7** Test that two runs of the runner with identical `(agent_id, seed, input)` produce byte-identical event sequences (step count, types, tokens, costs, failures, retries — everything except wall-clock timestamps). This is PRD §7 Phase 2's gate, test #1.
  **Depends on:** M3.T6
  - [ ] M3.T7.1 Run the same recipe twice, diff the full event lists field-by-field excluding timestamps.
        **Depends on:** M3.T6
  - [ ] M3.T7.2 Repeat across all three profiles and at least one seed that's known to trigger a non-retryable failure (flaky profile).
        **Depends on:** M3.T7.1

---

## Milestone 4 — Core API

Goal: `POST /v1/runs`, reads, list/filter/pagination, error envelope, startup recovery. *Done when:* HTTP lifecycle + recovery tests pass (PRD §7 Phase 3).

### Task M4.T1 — Request/response schemas
- [ ] **M4.T1** Define Pydantic v2 schemas in `app/api/schemas.py`: `RunCreateRequest` (`agent_id`, `input` ≤32KB, optional `seed`, optional `metadata: dict[str,str]`), `RunEnvelope` (status, recipe echoed back, `tokens`, `cost_usd`, `trace_id`, timestamps), `StepOut`, `ErrorEnvelope` (`type, code, message, param, request_id`), `RunListResponse` (`data, has_more, next_cursor`).
  **Depends on:** M1.T1, M1.T3
  - [ ] M4.T1.1 Define `RunCreateRequest` with the 32KB input-size validator.
        **Depends on:** M1.T1
  - [ ] M4.T1.2 Define `RunEnvelope` and `StepOut` mirroring the persistence projections (M2.T1). `RunEnvelope.trace_id` is always present from `pending` onward — it's generated at creation (M4.T3.1, amendment 2), not populated once telemetry runs.
        **Depends on:** M1.T1
  - [ ] M4.T1.3 Define `ErrorEnvelope` with `type` as a closed `Literal` set (`invalid_request, not_found, conflict, idempotency_error, internal_error`) and `RunListResponse`.
        **Depends on:** M1.T1

### Task M4.T2 — Error envelope & exception handlers
- [ ] **M4.T2** Register FastAPI exception handlers so every error response — including validation errors — uses the one `ErrorEnvelope` shape. Override the default 422 handler (PRD §2, §3.3). Every response carries `request_id` (generated per-request, also attached to the trace in Milestone 6). 5xx bodies never leak internals — map unexpected exceptions to a generic `internal_error` message, log the real one server-side.
  **Depends on:** M4.T1
  - [ ] M4.T2.1 Implement `request_id` middleware (generate or propagate from an incoming header, stash in request state).
        **Depends on:** M4.T1
  - [ ] M4.T2.2 Override `RequestValidationError` handler to emit `ErrorEnvelope` with `type: invalid_request`.
        **Depends on:** M4.T2.1
  - [ ] M4.T2.3 Add a catch-all handler for unhandled exceptions → `internal_error`, using `starlette.status` constants everywhere (no hardcoded status ints, per PRD §2).
        **Depends on:** M4.T2.1
  - [ ] M4.T2.4 Define domain-error → HTTP-status mapping (e.g. terminal-run conflict → 409, not-found → 404) as one lookup table.
        **Depends on:** M4.T2.2

### Task M4.T3 — Run service (durable-first execution)
- [ ] **M4.T3** Implement `app/services/run_service.py::create_run`: persist the run as `pending` via the repository (M2.T3) *before* touching the runner, then spawn `asyncio.Task(execute_run(...))` that drives the runner (M3.T6) and appends each yielded event via the repository. Document the queue-worker seam at this call site with a one-line comment, per PRD §4. This ordering is what makes "the response never lies" true (PRD §3.1). Also generate the `trace_id` here (M0.T9) and persist it as part of the same `pending` insert — the envelope's `trace_id` and the eventual root span's trace ID are therefore guaranteed to agree, since the span is started *with* this pre-generated ID in M7.T2.1 rather than minting its own (amendment 2).
  **Depends on:** M2.T3, M3.T6, M1.T1, M0.T9
  - [ ] M4.T3.1 Implement `create_run`: generate the run's ULID (M2.T4) and its `trace_id` (M0.T9), persist `pending` with both in the same insert, return the persisted envelope immediately.
        **Depends on:** M2.T4, M2.T3, M0.T9
  - [ ] M4.T3.2 Implement `execute_run` as the `asyncio.Task` body: iterate the runner's event generator, call `repository.append_event` for each, stop cleanly on completion/failure.
        **Depends on:** M3.T6, M2.T3
  - [ ] M4.T3.3 Wire `create_run` to spawn `execute_run` as a fire-and-forget task tracked in an in-process registry (needed later so the cancel endpoint can signal the in-flight `asyncio.Task` promptly; the authoritative `cancelling` status itself is always persisted via the repository, never read from this registry — amendment 1, see M5.T5).
        **Depends on:** M4.T3.1, M4.T3.2

### Task M4.T4 — POST /v1/runs
- [ ] **M4.T4** Implement the create endpoint: calls `run_service.create_run`, returns `202 Accepted` with a `Location: /v1/runs/{id}` header and the `pending` envelope body (PRD §3.1, §5 step 1).
  **Depends on:** M4.T3, M4.T2
  - [ ] M4.T4.1 Implement the route handler and response headers.
        **Depends on:** M4.T3
  - [ ] M4.T4.2 Confirm error paths (missing `agent_id`, oversized `input`) return the `ErrorEnvelope` via M4.T2.
        **Depends on:** M4.T4.1, M4.T2

### Task M4.T5 — GET /v1/runs/{id}
- [ ] **M4.T5** Implement the run-read endpoint: folds the run projection from the repository into a `RunEnvelope`, 404 via the shared error envelope if not found.
  **Depends on:** M2.T3, M4.T1, M4.T2
  - [ ] M4.T5.1 Implement the route handler and 404 mapping.
        **Depends on:** M2.T3, M4.T2

### Task M4.T6 — GET /v1/runs/{id}/steps
- [ ] **M4.T6** Implement the steps-read endpoint: returns per-step state (type, status, attempt count, `last_error`, per-step tokens/cost) from the `steps` projection.
  **Depends on:** M2.T3, M4.T1, M4.T2
  - [ ] M4.T6.1 Implement the route handler.
        **Depends on:** M2.T3, M4.T2

### Task M4.T7 — GET /v1/runs (list, filter, paginate)
- [ ] **M4.T7** Implement the list endpoint wired to `repository.list_runs` (M2.T5): cursor pagination, filters `status`, `agent_id`, `metadata.<key>`, `created_after/before`, fixed sort `created_at desc`.
  **Depends on:** M2.T5, M4.T1, M4.T2
  - [ ] M4.T7.1 Implement query-param parsing and validation for cursor + filters.
        **Depends on:** M4.T1
  - [ ] M4.T7.2 Wire to `list_runs`, return `RunListResponse`.
        **Depends on:** M4.T7.1, M2.T5

### Task M4.T8 — Startup recovery
- [ ] **M4.T8** On app startup, scan the repository for orphaned non-terminal runs — `pending`, `running`, and, since `cancelling` is now a persisted status (amendment 1), `cancelling` too — left over from a prior process that died, and resolve each to `failed` with error code `interrupted_by_restart`, appending a terminal event through the normal `append_event` path so it's indistinguishable from any other terminal write (PRD §3.1).
  **Depends on:** M2.T3, M1.T2, M0.T4
  - [ ] M4.T8.1 Implement `recover_orphaned_runs()` querying all non-terminal runs at boot (`pending`, `running`, `cancelling`).
        **Depends on:** M2.T3
  - [ ] M4.T8.2 For each, append a `RunFailed` event with `interrupted_by_restart` via the repository.
        **Depends on:** M4.T8.1, M1.T2
  - [ ] M4.T8.3 Wire `recover_orphaned_runs()` into the FastAPI startup event/lifespan.
        **Depends on:** M4.T8.2, M0.T4

### Task M4.T9 — HTTP integration tests (lifecycle + recovery)
- [ ] **M4.T9** Integration-test the full HTTP surface with `httpx.AsyncClient`: create → poll to completion, list/filter/pagination correctness, 404/422/409 error shapes, and a recovery test that seeds an orphaned `running` row directly in the DB, restarts the app, and asserts it flips to `failed`. This is PRD §7 Phase 3's gate.
  **Depends on:** M4.T4, M4.T5, M4.T6, M4.T7, M4.T8
  - [ ] M4.T9.1 Test create → poll-until-terminal happy path for each of the three profiles.
        **Depends on:** M4.T4, M4.T5
  - [ ] M4.T9.2 Test list pagination and each filter independently.
        **Depends on:** M4.T7
  - [ ] M4.T9.3 Test error envelope shape on 404 (unknown run) and 422 (bad payload).
        **Depends on:** M4.T5, M4.T4
  - [ ] M4.T9.4 Test startup recovery end to end.
        **Depends on:** M4.T8

---

## Milestone 5 — SSE Streaming & Cancellation

Goal: resumable live follow and clean cancellation. *Done when:* SSE-resume and cancel tests pass (PRD §7 Phase 4 — **end of day-1 target**).

### Task M5.T1 — SSE endpoint (log tail)
- [ ] **M5.T1** Implement `GET /v1/runs/{id}/events`: streams events from the repository starting at sequence 1 (amendment 3), formatted as SSE (`id:`, `event:`, `data:` per message), content-type `text/event-stream`. Reads are purely from the persisted log — the endpoint never touches the runner directly (PRD §3.1, §5).
  **Depends on:** M4.T5, M2.T3
  - [ ] M5.T1.1 Implement the SSE response generator reading historical events from the repository first.
        **Depends on:** M2.T3
  - [ ] M5.T1.2 Implement the "live tail" continuation: after historical events are exhausted, poll/subscribe for new events until the run reaches a terminal state, then close the stream.
        **Depends on:** M5.T1.1

### Task M5.T2 — Last-Event-ID resume
- [ ] **M5.T2** Honor an incoming `Last-Event-ID: N` header: resume from sequence `N+1`, byte-identical to a fresh replay from that point (PRD §3.1). Correctness follows directly from the stream being a tail of the persisted log — no separate resume logic needed beyond parameterizing the starting sequence. Since event sequences are 1-indexed (amendment 3), an absent header or `Last-Event-ID: 0` both naturally mean "replay from the beginning" — no special-casing required.
  **Depends on:** M5.T1
  - [ ] M5.T2.1 Parse `Last-Event-ID` and pass `after_sequence=N` into `get_events_from`.
        **Depends on:** M5.T1, M2.T3

### Task M5.T3 — Heartbeats
- [ ] **M5.T3** Emit an SSE comment (`: heartbeat`) every 15s on idle streams to defeat proxy/load-balancer buffering timeouts (PRD §6).
  **Depends on:** M5.T1
  - [ ] M5.T3.1 Implement the heartbeat timer interleaved with the event-tail loop.
        **Depends on:** M5.T1

### Task M5.T4 — In-process event notification
- [ ] **M5.T4** Implement a lightweight in-process pub/sub (e.g. one `asyncio.Queue`/`asyncio.Condition` per active run) so the SSE live-tail (M5.T1.2) doesn't busy-poll the DB: `run_service.execute_run` (M4.T3) notifies subscribers immediately after each `append_event`.
  **Depends on:** M4.T3, M5.T1
  - [ ] M5.T4.1 Implement a per-run subscriber registry (`dict[run_id, list[asyncio.Queue]]`) in `app/services/`.
        **Depends on:** M4.T3
  - [ ] M5.T4.2 Wire `execute_run` to push onto subscriber queues after each successful `append_event`.
        **Depends on:** M5.T4.1
  - [ ] M5.T4.3 Wire the SSE live-tail to subscribe/unsubscribe around its poll loop, falling back to a short poll interval if no subscription exists (defensive, not the primary path).
        **Depends on:** M5.T4.1, M5.T1.2

### Task M5.T5 — POST /v1/runs/{id}/cancel
- [ ] **M5.T5** Implement the cancel endpoint: on a non-terminal run, persist a `cancelling` status transition via the repository (the same durable `append_event` path as every other status change, not an in-memory flag — amendment 1), return `202`; on an already-terminal run, return `409` via the shared error envelope. "First terminal write wins" — this endpoint only ever requests cancellation, it never itself writes a terminal state (PRD §3.1).
  **Depends on:** M4.T3, M4.T2, M1.T1
  - [ ] M5.T5.1 Implement the route + service call that appends a `cancelling` status transition through the repository — the persisted source of truth, readable by any poller even without access to the in-process task registry — then separately signals the running `asyncio.Task` via the in-process registry (M4.T3.3) so the runner notices promptly instead of waiting for its next step boundary.
        **Depends on:** M4.T3
  - [ ] M5.T5.2 Implement the 409-on-terminal check using `is_terminal` (M1.T1).
        **Depends on:** M5.T5.1, M1.T1

### Task M5.T6 — Runner cancellation semantics
- [ ] **M5.T6** Wire the cancel signal into `execute_run`/the runner's cancellation checkpoint (M3.T6.2): checked at each step boundary; on trip, a `finally` block guarantees a terminal `RunCancelled` event is appended and (later, Milestone 7) the root span is closed, regardless of where in the step plan execution was. The checkpoint reads the fast in-memory signal for responsiveness, but the persisted `cancelling` status (M5.T5.1) remains authoritative — a process that restarts mid-cancel must still observe `cancelling` from the store and drive the run to a terminal state (folded into recovery, M4.T8; amendment 1).
  **Depends on:** M3.T6, M5.T5
  - [ ] M5.T6.1 Implement the checkpoint check inside `execute_run`'s event loop (checks the in-memory signal set alongside the persisted write in M5.T5.1).
        **Depends on:** M3.T6.2, M5.T5.1
  - [ ] M5.T6.2 Implement the `finally` block guaranteeing the terminal event append even on an unexpected exception mid-step.
        **Depends on:** M5.T6.1

### Task M5.T7 — SSE resume & cancellation tests
- [ ] **M5.T7** Integration-test: start a run, connect SSE, disconnect mid-stream, reconnect with `Last-Event-ID`, assert exactly the missed events replay and nothing duplicates; separately, cancel a running run and assert `cancelling → cancelled` appears on the stream and a second cancel returns 409. This is PRD §7 Phase 4's gate.
  **Depends on:** M5.T2, M5.T3, M5.T5, M5.T6
  - [ ] M5.T7.1 Test disconnect/reconnect-with-resume produces a byte-identical union with a fresh full replay.
        **Depends on:** M5.T2
  - [ ] M5.T7.2 Test cancel-while-running reaches `cancelled` and a second cancel call 409s.
        **Depends on:** M5.T5, M5.T6

---

## Milestone 6 — Idempotency

Goal: safe retries of `POST /v1/runs`. *Done when:* idempotency tests pass, including the concurrent-race case (PRD §7 Phase 5, test #5).

### Task M6.T1 — Idempotency key table
- [ ] **M6.T1** Add an `idempotency_keys` table (`key`, `request_hash`, `run_id`, `created_at`) with a `UNIQUE` constraint on `key`, migrated alongside M2.T1's schema (PRD §3.3).
  **Depends on:** M2.T1
  - [ ] M6.T1.1 Write the migration adding the table and unique index.
        **Depends on:** M2.T1

### Task M6.T2 — Idempotent create path
- [ ] **M6.T2** In `create_run` (M4.T3) / the `POST /v1/runs` handler (M4.T4), when `Idempotency-Key` is present: hash the request body, look up the key. Not found → proceed normally, then store `key → (run_id, request_hash)`. Found with matching hash → replay the original stored response verbatim, no new run created. Found with differing hash → `409` with `type: idempotency_error`.
  **Depends on:** M6.T1, M4.T4
  - [ ] M6.T2.1 Implement request-body hashing (stable JSON canonicalization → hash).
        **Depends on:** M6.T1
  - [ ] M6.T2.2 Implement the lookup-then-branch logic (replay / proceed / conflict).
        **Depends on:** M6.T2.1, M4.T4
  - [ ] M6.T2.3 Implement response replay: store enough of the original response to reconstruct it byte-identical on replay.
        **Depends on:** M6.T2.2

### Task M6.T3 — Concurrent-race handling
- [ ] **M6.T3** Handle two simultaneous requests with the same new key: both attempt the insert, the DB's `UNIQUE` constraint lets exactly one win; the loser catches the `IntegrityError`, re-reads the now-present row, and replays that response instead of erroring (PRD §3.3 — "the concurrent race is decided by a `UNIQUE` constraint").
  **Depends on:** M6.T2
  - [ ] M6.T3.1 Wrap the key-insert in a try/except that, on unique-violation, falls back to the read-and-replay path from M6.T2.3.
        **Depends on:** M6.T2

### Task M6.T4 — Key expiry (documented)
- [ ] **M6.T4** Implement lazy 24h expiry: on lookup, treat a key older than 24h as not-found (allow a fresh run to be created and the key row overwritten/replaced). No background sweeper needed at this scale — document that choice inline.
  **Depends on:** M6.T1
  - [ ] M6.T4.1 Add the age check to the lookup path in M6.T2.2.
        **Depends on:** M6.T2, M6.T1

### Task M6.T5 — Idempotency tests
- [ ] **M6.T5** Test: same key + same body twice → same run, second call doesn't re-execute; same key + different body → 409 `idempotency_error`; two concurrent requests with the same new key → exactly one run created, both callers get the same response. This is PRD §7 Phase 5's test #5.
  **Depends on:** M6.T2, M6.T3, M6.T4
  - [ ] M6.T5.1 Test replay-on-match.
        **Depends on:** M6.T2
  - [ ] M6.T5.2 Test conflict-on-mismatch.
        **Depends on:** M6.T2
  - [ ] M6.T5.3 Test the concurrent-race case with two tasks firing simultaneously against the same key.
        **Depends on:** M6.T3

---

## Milestone 7 — Telemetry (OpenTelemetry)

Goal: one legible trace per run plus cardinality-disciplined metrics. *Done when:* in-memory-exporter assertions pass and a live run renders correctly in Tempo (PRD §7 Phase 6).

### Task M7.T1 — OTel SDK setup
- [ ] **M7.T1** Implement `app/telemetry/setup.py`: configure `TracerProvider` and `MeterProvider` with an OTLP exporter pointed at `Settings.otel_endpoint` (M0.T3), resource attributes (`service.name`, `service.version`), and a pluggable exporter (real OTLP in prod, in-memory in tests per PRD §6).
  **Depends on:** M0.T3, M0.T4
  - [ ] M7.T1.1 Implement `configure_tracing(settings)` returning a configured `TracerProvider`.
        **Depends on:** M0.T3
  - [ ] M7.T1.2 Implement `configure_metrics(settings)` returning a configured `MeterProvider`.
        **Depends on:** M0.T3
  - [ ] M7.T1.3 Wire both into FastAPI's lifespan/startup in `app/main.py`, with an `InMemorySpanExporter`/`InMemoryMetricReader` swap-in used by the test fixture.
        **Depends on:** M7.T1.1, M7.T1.2, M0.T4

### Task M7.T2 — Span tree helpers
- [ ] **M7.T2** Implement span helpers in `app/telemetry/spans.py`: one root `run` span per run, one child span per step, sub-agent steps nested one level deeper, and — critically — **each retry attempt gets its own child span**, so retry latency/backoff shows as bars and gaps in the waterfall (PRD §3.4). The root span must adopt the run's already-persisted `trace_id` (M4.T3.1) rather than letting the SDK mint a fresh one — see M7.T2.1 (amendment 2).
  **Depends on:** M7.T1, M1.T3
  - [ ] M7.T2.1 Implement `start_run_span(run)` opening the root span using `run.trace_id` (persisted at creation, M4.T3.1) as its trace ID — construct an explicit `SpanContext`/`NonRecordingSpan` carrying that trace ID and start the real root span inside that context, so OTel adopts the stored ID instead of generating a new one. Set `stackai.run.id` (bidirectional linkage, PRD §3.4).
        **Depends on:** M7.T1, M4.T3.1
  - [ ] M7.T2.2 Implement `start_step_span(parent, step)` for the standard per-step child span.
        **Depends on:** M7.T2.1
  - [ ] M7.T2.3 Implement `start_attempt_span(step_span, attempt_number)` — one per retry attempt, child of the step span.
        **Depends on:** M7.T2.2
  - [ ] M7.T2.4 Implement sub-agent nesting: a sub-agent step's span is the parent of its own child step spans, exactly one level deep.
        **Depends on:** M7.T2.2

### Task M7.T3 — GenAI + stackai.* attributes
- [ ] **M7.T3** Attach OTel GenAI semantic-convention attributes (model name, token counts) plus the `stackai.*` namespace (`stackai.run.id`, `stackai.agent_id`, `stackai.cost_usd`, etc.) to the relevant spans; set span status to error on failed attempts with the structured error (M1.T2) recorded. Put `trace_id` on the `RunEnvelope` (M4.T1) so the API↔trace link is bidirectional.
  **Depends on:** M7.T2, M1.T2, M4.T1
  - [ ] M7.T3.1 Define the attribute-setting helper applied to model-call step spans (GenAI conventions).
        **Depends on:** M7.T2
  - [ ] M7.T3.2 Define the `stackai.*` attribute helper applied to every span.
        **Depends on:** M7.T2
  - [ ] M7.T3.3 On step/attempt failure, set span status `ERROR` and record the `RunError` as a span event/attributes.
        **Depends on:** M7.T3.1, M1.T2
  - [ ] M7.T3.4 Add a test asserting the root span's actual OTel trace ID equals `run.trace_id` as already persisted at creation (M4.T3.1) — confirming M7.T2.1's explicit-context technique works, rather than writing the span's ID back into the store (the API never reads from OTel, PRD §2; amendment 2).
        **Depends on:** M7.T2.1, M4.T1

### Task M7.T4 — Metric instruments
- [ ] **M7.T4** Implement the five metric instruments in `app/telemetry/metrics.py`, deliberately excluding high-cardinality labels (`run_id`, `metadata.*`) per PRD §3.4: `runs.completed` (labels `agent_id, status`), `run.duration` histogram (`agent_id`), `tokens.used` (`agent_id, direction`), `cost.usd` (`agent_id`), `steps.executed` (`step_type, outcome, agent_id`).
  **Depends on:** M7.T1
  - [ ] M7.T4.1 Define all five instruments with correct types (counter vs. histogram) and label sets.
        **Depends on:** M7.T1
  - [ ] M7.T4.2 Write a unit test asserting none of the instrument-recording call sites pass `run_id` or a `metadata.*` key as a label — the cardinality-discipline guarantee.
        **Depends on:** M7.T4.1

### Task M7.T5 — Wire telemetry into execution path
- [ ] **M7.T5** Instrument `execute_run` (M4.T3) / the runner's event loop (M3.T6) so each event yields exactly one instrumentation point: open/close the appropriate span, record the appropriate metrics, on the cancellation path (M5.T6) close the root span in the same `finally` block that guarantees the terminal event.
  **Depends on:** M7.T2, M7.T4, M4.T3, M5.T6
  - [ ] M7.T5.1 Wire root span open (on `RunStarted`) / close (on any terminal event) into `execute_run`.
        **Depends on:** M7.T2.1, M4.T3
  - [ ] M7.T5.2 Wire step/attempt span open/close into the per-step handling in `execute_run`.
        **Depends on:** M7.T2.2, M7.T2.3
  - [ ] M7.T5.3 Wire metric recording (duration, tokens, cost, outcome) at the same points.
        **Depends on:** M7.T4.1, M7.T5.2
  - [ ] M7.T5.4 Extend the cancellation `finally` block (M5.T6.2) to also close the root span with the correct status.
        **Depends on:** M5.T6.2, M7.T5.1

### Task M7.T6 — In-memory exporter tests
- [ ] **M7.T6** Using the `InMemorySpanExporter`/`InMemoryMetricReader` from M7.T1.3, assert: root span exists with correct `stackai.run.id`; step count matches the step plan; retry attempts produce distinct child spans; failed attempts carry `ERROR` status; all five metrics recorded with correct label sets and no forbidden high-cardinality labels. "We test our instrumentation, not the vendor" (PRD §6).
  **Depends on:** M7.T5
  - [ ] M7.T6.1 Test span tree shape (root → step → attempt, and sub-agent nesting) for a run with at least one retry.
        **Depends on:** M7.T5
  - [ ] M7.T6.2 Test metric emission and label correctness for all five instruments.
        **Depends on:** M7.T5

### Task M7.T7 — Live Grafana Cloud smoke test
- [ ] **M7.T7** With real (or debug-fallback) collector config from M0.T6, run the app against `docker compose up`, execute a real run, and manually confirm the trace renders correctly in Tempo (or the collector's debug log if no Grafana Cloud account is configured) — waterfall shape, attributes, error status all visible.
  **Depends on:** M7.T5, M0.T6
  - [ ] M7.T7.1 Run one instance of each profile through the live stack.
        **Depends on:** M7.T5, M0.T6
  - [ ] M7.T7.2 Confirm in Tempo/debug log: root span, per-step children, per-attempt children on the flaky profile, GenAI + `stackai.*` attributes present.
        **Depends on:** M7.T7.1

### Task M7.T8 — Grafana Cloud credentials wiring
- [ ] **M7.T8** Document and wire the Grafana Cloud OTLP endpoint + API key into `.env` / `.env.example` (M0.T3.2), confirming the system still runs fully via the debug exporter with these unset (PRD §4 — "the system runs fully without any account").
  **Depends on:** M7.T7
  - [ ] M7.T8.1 Add the Grafana Cloud env vars to `.env.example` with setup instructions in a comment.
        **Depends on:** M7.T7
  - [ ] M7.T8.2 Confirm `docker compose up` with these vars empty still serves traces via the `debug` exporter (no crash, no silent drop).
        **Depends on:** M7.T8.1

---

## Milestone 8 — Dashboard & Demo Data

Goal: a customer-actionable analytics view plus one-command demo seeding (PRD §7 Phase 7).

### Task M8.T1 — Dashboard panels
- [x] **M8.T1** Build the six-panel Grafana dashboard (as committed JSON, PRD §3.4): cost over time by agent; output-token share by agent; run outcome rates by agent; step failure/retry rate by step type; duration p50/p95; cost per completed run. Each panel should make the "implied action" from PRD §3.4 legible at a glance.
  **Depends on:** M7.T4, M7.T7
  - [x] M8.T1.1 Build the cost-over-time-by-agent panel (`cost.usd` counter, rate by `agent_id`).
        **Depends on:** M7.T4
  - [x] M8.T1.2 Build the output-token-share-by-agent panel (`tokens.used` split by `direction`).
        **Depends on:** M7.T4
  - [x] M8.T1.3 Build the run-outcome-rate-by-agent panel (`runs.completed` by `status, agent_id`).
        **Depends on:** M7.T4
  - [x] M8.T1.4 Build the step failure/retry-rate-by-step-type panel (`steps.executed` by `step_type, outcome`).
        **Depends on:** M7.T4
  - [x] M8.T1.5 Build the duration p50/p95 panel (`run.duration` histogram quantiles).
        **Depends on:** M7.T4
  - [x] M8.T1.6 Build the cost-per-completed-run panel (`cost.usd` / `runs.completed{status=completed}`).
        **Depends on:** M7.T4
  - [x] M8.T1.7 Export and commit the dashboard as JSON (provisioning-friendly) in `grafana/dashboard.json`.
        **Depends on:** M8.T1.1, M8.T1.2, M8.T1.3, M8.T1.4, M8.T1.5, M8.T1.6
        **Gate findings fixed (rev 2):** all panels switched from `rate(...[$__rate_interval])` to `sum by (...) (increase(...[$__range]))` (and `histogram_quantile` fed by `increase()` instead of `rate()`) — `make demo` seeds a one-off burst of 4 runs, not continuous traffic, so `rate()` normalized everything down to a near-zero per-second value once it aged past the short default rate-interval lookback: empty duration panel, $0 cost panels, 0-value "Total" outcome legend were all the same root cause. `increase()` over the dashboard's own visible range stays populated for exactly this kind of low-volume/bursty dataset. Also: `currencyUSD` fields now set `decimals: 6` (costs are fractions of a cent) and legend calcs changed from `sum`/`sum,last` to `lastNotNull`/`lastNotNull,max` — summing a flat `increase()`-over-`$__range` series across its own re-evaluated points over-counts; `lastNotNull` reads the actual accumulated value.
        **Gate findings fixed (rev 3, M9.T6):** legend "Last" still read 0 on the outcome-rate/step-failure panels, and `agent-simple`'s cost panel rendered `$0.000000`, despite real non-zero data. Root-caused by live-checking `docker compose logs collector`: every instrument is a `Cumulative` OTel sum that's genuinely non-zero at the source (confirmed `cost_usd_total{agent_id="agent-simple"} = 0.020060`, `runs_completed_total{agent_id="agent-simple",status="completed"} = 1`) — the bug was in `increase(counter[$__range])` itself, not the instrumentation. `make demo` produces exactly one increment per counter series; Prometheus's `increase()` extrapolates over so few samples that it can round a true value of `1` down to `0` (a documented PromQL pitfall on sparse/bursty series, distinct from the rev-2 `rate()` problem). Fixed by reading the raw cumulative counter directly — `sum by (...) (metric_total)`, no `increase()`/`rate()` wrapper — on panels 1, 2, 3, 4, 6; panel 5 (duration p50/p95) still needs `increase()` feeding `histogram_quantile`, the only valid way to compute a quantile from bucket counts. Panels 3/4 also switched `drawStyle` from `bars` to `line`+`fillOpacity` (stacked area) since a raw cumulative value plotted as bars read as a solid block rather than a legible trend.

### Task M8.T2 — Exemplars on the p95 panel
- [ ] **M8.T2** Wire exemplar support on the duration histogram (requires exemplars enabled in the OTel metrics SDK config and Tempo↔Prometheus linkage in Grafana Cloud) so clicking a p95 data point jumps to the actual slow trace (PRD §3.4, §5 step 7).
  **Depends on:** M8.T1, M7.T3
  - [x] M8.T2.1 Enable exemplar recording on the `run.duration` histogram instrument. Root-span span-context is now threaded explicitly into `Metrics.record_run_duration` (captured before the span ends in `RunTracer._on_run_terminal`) since spans here are opened via `tracer.start_span`, never `start_as_current_span` — there's no ambient "current span" for the SDK's default `TraceBasedExemplarFilter` to find otherwise. Verified live: `docker compose logs collector` shows a real `Exemplar` block with the correct `trace_id`/`span_id` on every `run.duration` histogram data point after `make demo`.
        **Depends on:** M8.T1.5
        **Gate finding fixed:** the provisioned Grafana Cloud Prometheus data source is read-only and its exemplar-to-trace link expects a `traceID` label; OTel's spec hardcodes the *native* exemplar trace-ID label as `trace_id` (`prometheus/otlptranslator`'s `ExemplarTraceIDKey`) — not renameable via collector config. Fixed by attaching a redundant `traceID` measurement attribute in `record_run_duration`, kept off the metric's real (bounded) label set via a new `View(instrument_name="run.duration", attribute_keys={"agent_id"})` in `app/telemetry/setup.py` (`RUN_DURATION_VIEW`), which demotes it to the exemplar's `filtered_attributes` instead. Verified live in collector logs: `FilteredAttributes: -> traceID: Str(<run's real trace_id>)`, with `Data point attributes: agent_id: Str(...)` only — cardinality untouched. Also confirmed no unit bug: `Sum`/`Exemplar Value` on `run_duration_milliseconds_bucket` matched real per-run wall-clock durations (e.g. `9779`, `2719`) — the previously-empty duration panel was a query problem (see M8.T1.7 dashboard update below), not an instrumentation one.
  - [ ] M8.T2.2 Configure the Grafana Cloud data source linkage (Prometheus metric → Tempo trace) and confirm click-through works.
        **Depends on:** M8.T2.1, M7.T7

### Task M8.T3 — `make demo` seed script
- [x] **M8.T3** Implement the demo-data script wired into `make demo` (completing the M0.T8.3 stub): create runs across all three profiles with fixed, known seeds; guarantee at least one run that ends `failed` and one that ends `cancelled` (by choosing a seed known to fail, and by issuing a real cancel request mid-run), so the dashboard and demo flow always have interesting data.
  **Depends on:** M4.T4, M6.T2, M5.T5, M3.T7, M0.T8
  - [x] M8.T3.1 Pick and hardcode seeds (from M3.T7's determinism tests) known to produce: a clean success per profile, and a flaky-profile non-retryable failure.
        **Depends on:** M3.T7
  - [x] M8.T3.2 Script POSTs for each seeded run via the real HTTP API (`httpx` against the running compose stack).
        **Depends on:** M4.T4, M8.T3.1
  - [x] M8.T3.3 Script issues a cancel request against one in-flight run shortly after creation.
        **Depends on:** M5.T5, M8.T3.2
  - [x] M8.T3.4 Wire the script into `make demo`, replacing the M0.T8.3 stub.
        **Depends on:** M8.T3.2, M8.T3.3, M0.T8

### Task M8.T4 — End-to-end demo verification
- [ ] **M8.T4** Run `make demo` against a fresh `docker compose up`, confirm every dashboard panel populates with non-trivial data and the exemplar click-through lands on a real trace.
  **Depends on:** M8.T1, M8.T2, M8.T3
  - [ ] M8.T4.1 Run the full sequence from a clean state and screenshot each panel for later use in the README (M9.T2).
        **Depends on:** M8.T1, M8.T2, M8.T3

---

## Milestone 9 — Product Polish

Goal: everything a grader/integrator needs to trust and use the system unassisted (PRD §7 Phase 8).

### Task M9.T1 — OpenAPI spec review & commit
- [x] **M9.T1** Review the FastAPI-generated OpenAPI schema for completeness (every endpoint documented, every error response modeled, examples present on key schemas), then export and commit `openapi.json` at the repo root, matching PRD §2's "shippable OpenAPI spec" requirement.
  **Depends on:** M4.T9, M6.T5, M5.T7
  - [x] M9.T1.1 Add/adjust FastAPI route metadata (`summary`, `description`, `responses=`) so `/docs` reads as documentation, not a schema dump. Also fixed a real accuracy bug found along the way: FastAPI's default 422 response documented its own `HTTPValidationError` shape, but the app's exception handlers rewrite every error — including validation failures — into `ErrorEnvelope`. Every route's `responses=` now points 404/409/422 at `ErrorEnvelope` so `/docs` matches the actual wire format. Added examples to `RunCreateRequest` and `ErrorEnvelope`, field-level descriptions on the envelope/step/error schemas, an app-level description, and an `openapi_tags` entry for `runs`.
        **Depends on:** M4.T9
  - [x] M9.T1.2 Export `openapi.json` via a script (`app.openapi()` dumped to file) and commit it. Added `scripts/export_openapi.py` + `make openapi` target for regenerating it after future route/schema changes.
        **Depends on:** M9.T1.1

### Task M9.T2 — README
- [x] **M9.T2** Write the README: quickstart (`make up`, two commands, no accounts required) → `make demo` walkthrough → architecture diagram (M9.T3) → one-line rationale per major decision (pointing back to PRD §2's defensibility principle) → curl tour of the API → screenshots from M8.T4.1.
  **Depends on:** M8.T4, M9.T1
  - [x] M9.T2.1 Write the quickstart and demo sections, verified by actually running them from a clean checkout.
        **Depends on:** M8.T4
  - [x] M9.T2.2 Write the one-line-decisions section, condensing PRD §2/§4/§6's "alternative it beat" framing.
        **Depends on:** M9.T1
  - [x] M9.T2.3 Write the curl tour (create → poll → SSE follow → cancel → idempotent retry) and embed the M8.T4.1 screenshots. **Screenshot note:** the three M8.T4.1 screenshots (researcher waterfall, flaky retry trace, dashboard) weren't available in this pass — the files found under the expected path were two dashboard bug-repro shots and one unrelated Docker Desktop settings screenshot. Per user decision, the README embeds `docs/images/{researcher-waterfall,flaky-retry-trace,dashboard}.png` as placeholders (see `docs/images/README.md` for what each should show); to be filled in after M9.T6's dashboard fix.
        **Depends on:** M9.T2.1

### Task M9.T3 — Architecture diagram
- [x] **M9.T3** Produce an architecture diagram (Mermaid, embedded in the README, or a committed image) covering PRD §4's component table and §5's internal data-flow: API → service → {persistence, runner} → {SQLite, OTel Collector → Grafana}.
  **Depends on:** M7.T8, M8.T4
  - [x] M9.T3.1 Draft the diagram covering all six components from PRD §4. Embedded as a Mermaid `flowchart TB` in the README's Architecture section, paired with the component table from PRD §4.
        **Depends on:** M7.T8, M8.T4

### Task M9.T4 — Demo rehearsal & defense prep
- [x] **M9.T4** Rehearse the full developer flow from PRD §5 end to end against the shipped README, and prepare crisp answers to the five defense questions named in PRD §7 Phase 8: event log & projections, SSE resume, durable-first lifecycle, spans vs. events, metric cardinality.
  **Depends on:** M9.T2, M9.T3
  - [x] M9.T4.1 Walk PRD §5 steps 1–8 verbatim against the running system, noting any friction. Ran live against `docker compose up` + the exact curl tour in the README: create (202 + Location + trace_id), SSE follow, disconnect/resume with `Last-Event-ID` (verified byte-exact continuation at sequence 6), poll, steps, cancel (202 → `cancelled`, second cancel → 409), idempotent retry (same key+body → identical `id`/`created_at` twice; different body → 409). No friction found — every step worked exactly as documented on the first try.
        **Depends on:** M9.T2
  - [x] M9.T4.2 Write one paragraph each for the five defense questions, citing the specific code/decision that answers them. See `docs/defense-questions.md`; each answer's code references (line numbers, test names) were verified against the actual source, not asserted from memory.
        **Depends on:** M9.T3

### Task M9.T6 — Response serialization & shape fixes (gate findings from M4)
- [x] **M9.T6** Fix two response issues surfaced during the M4 gate review's curl tour, plus two dashboard bugs surfaced during M9 polish.
  **Depends on:** M4.T9
  - [x] M9.T6.1 Round `cost_usd` to 6 decimal places at the serialization boundary (API schemas — not in storage, which should keep full precision). Envelopes/steps currently emit float noise like `0.08907000000000001`. Implemented via `field_serializer` on `RunEnvelope.cost_usd` and `StepOut.cost_usd` in `app/api/schemas.py`; verified live that storage keeps full precision (`0.020496999999999998` in SQLite) while the API rounds it (`0.020497`).
        **Depends on:** M4.T9
  - [x] M9.T6.2 `GET /v1/runs/{id}/steps` currently returns a bare JSON array. PRD §3.3 says list responses share the `{data, has_more, next_cursor}` shape. Decision: bring it in line — added `StepListResponse` (`app/api/schemas.py`) with `has_more` always `false` and `next_cursor` always `null` (steps are never paginated; a profile caps at ~8 top-level steps + one level of sub-agent nesting), so every list-shaped endpoint in the API shares one predictable envelope rather than a client needing a special case for "is this one paginated."
        **Depends on:** M4.T9
  - [x] M9.T6.3 *(new, found during M9 polish)* Dashboard legend "Last" calc read `0` on the outcome-rate/step-failure panels, and `agent-simple`'s cost panel rendered `$0.000000`, despite real non-zero data. Root-caused live via `docker compose logs collector`: every instrument is a `Cumulative` OTel sum, genuinely non-zero at the source — the bug was `increase(counter[$__range])` extrapolating a single-increment demo counter down toward 0 (a real PromQL pitfall on sparse series, distinct from the rev-2 `rate()` fix). Fixed by reading the raw cumulative counter directly (no `increase()`/`rate()`) on panels 1/2/3/4/6 in `grafana/dashboard.json` (rev 3); panel 5's `histogram_quantile` still needs `increase()`, which is mathematically required there. See the M8.T1.7 gate-findings note (rev 3) for full detail.
        **Depends on:** M4.T9

### Task M9.T5 — Final quality gate
- [x] **M9.T5** Run `make lint`, `make typecheck`, `make test` clean on a fresh checkout; remove any leftover TODOs/dead code introduced during earlier milestones.
  **Depends on:** M9.T1, M9.T4, M9.T6
  - [x] M9.T5.1 Fix any lint/type/test failures surfaced by a clean-checkout run. `ruff check .` clean, `mypy app` clean (39 source files), `pytest` (`SIM_SPEED=100`) 195/195 passing.
        **Depends on:** M9.T1, M9.T4
  - [x] M9.T5.2 Grep for `TODO`/`FIXME`/commented-out code and resolve or remove each. Only hit was the literal string "TODO" inside `docs/todo.md`'s own name/content (expected); no TODO/FIXME/XXX markers or dead code in `app/`, `tests/`, or `scripts/`.
        **Depends on:** M9.T5.1

---

## Milestone 10 — Stretch (only if Milestones 0–9 are green)

Goal: the deferred extensions named in PRD §3.5, built only if time remains.

### Task M10.T1 — `/runs/{id}/replay` endpoint
- [ ] **M10.T1** Implement `POST /v1/runs/{id}/replay`: reads the original run's `(agent_id, seed, input)`, calls `create_run` with that recipe, and tags the resulting run's root span with a `replayed_from` attribute pointing at the original run ID (PRD §3.5, §5 step 8).
  **Depends on:** M9.T5, M4.T3, M2.T5, M7.T2
  - [ ] M10.T1.1 Implement the route: look up the original recipe, call `create_run`.
        **Depends on:** M9.T5, M4.T3
  - [ ] M10.T1.2 Add the `replayed_from` span attribute on the new run's root span.
        **Depends on:** M10.T1.1, M7.T2.1
  - [ ] M10.T1.3 Test: replaying a run produces an identical step sequence to the original (leans on M3.T7's determinism guarantee).
        **Depends on:** M10.T1.1

### Task M10.T2 — ETag / If-None-Match on the envelope
- [ ] **M10.T2** Add an `ETag` header (hash of the envelope) to `GET /v1/runs/{id}` responses; honor `If-None-Match` with `304 Not Modified`, cutting poll bandwidth for long-lived clients (PRD §3.5).
  **Depends on:** M9.T5, M4.T5
  - [ ] M10.T2.1 Implement ETag computation and response header.
        **Depends on:** M9.T5, M4.T5
  - [ ] M10.T2.2 Implement `If-None-Match` handling → `304`.
        **Depends on:** M10.T2.1

### Task M10.T3 — Deferred-extension notes
- [ ] **M10.T3** Document (no code) the remaining deferred extensions named in PRD §3.5 — generated SDK snippet, sampling strategy, alerting, endpoint deprecation policy — each with the one-line cost that justified deferring it, appended to the README's decisions section (M9.T2.2).
  **Depends on:** M9.T5
  - [ ] M10.T3.1 Write the one-line-cost note for each of the four remaining deferred extensions.
        **Depends on:** M9.T5

---

## Dependency Overview (top-level tasks only)

```
M0.T1 → M0.T2 → M0.T3 → M0.T4 → M0.T5 ─┐
                              M0.T6 ────┼→ M0.T7 → M0.T8
                              M0.T2 → M0.T9
M0.T1 → M1.T1 → M1.T2 → M1.T3 → M1.T5
              → M1.T4 ───────────────┘
M1.T3 + M0.T2 → M2.T1 → M2.T2 → M2.T3 → M2.T5 → M2.T6
M2.T1 → M2.T4 ─────────────────────┘
M1.T4 → M3.T1 → M3.T2 → M3.T3 → M3.T4 → M3.T5 → M3.T6 → M3.T7
M1.T1 + M1.T3 → M4.T1 → M4.T2
M2.T3 + M3.T6 + M0.T9 → M4.T3 → M4.T4 → M4.T5, M4.T6, M4.T7, M4.T8 → M4.T9
M4.T5 → M5.T1 → M5.T2, M5.T3, M5.T4 → M5.T5 → M5.T6 → M5.T7
M2.T1 → M6.T1 → M6.T2 → M6.T3, M6.T4 → M6.T5
M0.T3 → M7.T1 → M7.T2 (uses M4.T3.1's stored trace_id) → M7.T3 ─┐
              M7.T4 ───────────────────────────────────────────┼→ M7.T5 → M7.T6, M7.T7 → M7.T8
M7.T4 + M7.T7 → M8.T1 → M8.T2 → M8.T3 → M8.T4
M4.T9 + M6.T5 + M5.T7 → M9.T1 → M9.T2 → M9.T3 → M9.T4 → M9.T5
M9.T5 → M10.T1, M10.T2, M10.T3   (stretch, optional)
```

## Suggested build order

1. **Day 1 morning:** Milestone 0 → Milestone 1 → Milestone 2.
2. **Day 1 midday:** Milestone 3 (runner) — can start once M1.T4 lands, in parallel with finishing M2.
3. **Day 1 afternoon:** Milestone 4 → Milestone 5. *(PRD's own "end of day 1" checkpoint.)*
4. **Day 2 morning:** Milestone 6 → Milestone 7.
5. **Day 2 midday:** Milestone 8.
6. **Day 2 afternoon:** Milestone 9, then Milestone 10 only if time remains.
