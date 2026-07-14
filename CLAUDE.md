# agent-service

## Summary

A developer starts an **agent run** — a multi-step execution involving model calls, tool calls, and occasionally sub-agents — through a public, versioned HTTP API, follows it live to completion, and reads back what happened and what it cost. Behind the API sits a **seeded, deterministic fake runner**; every step it executes is instrumented once and emitted to two sinks — an append-only SQLite event log (the product's source of truth) and OpenTelemetry spans/metrics exported through a local Collector to Grafana Cloud. Guiding sentence: **instrument once, serve three audiences** — the developer following a run (API + SSE), the operator investigating one (traces), and the customer acting on trends (analytics).

## Stack

Python 3.12+, FastAPI, Pydantic v2, pydantic-settings, aiosqlite, python-ulid, OpenTelemetry SDK. Fully async, typed throughout.

## Commands

- **run:** `docker compose up` (serves API on `:8000`, `/docs` for OpenAPI)
- **test:** `pytest` (`SIM_SPEED=100` is set for tests — runs finish in ms)
- **lint/type:** `make lint`, `make typecheck`
- **demo:** `make demo` (seeds all three profiles, one failure, one cancellation)

## Key docs

Full design in `docs/specs.md` — it is **LOCKED**; do not redesign or deviate without asking me first. Implementation plan with task IDs and dependencies in `todo.md` — work milestone by milestone, stop at each milestone gate for review.

## Invariants

Rules that apply to all code in this repo, never violate:

- Run behavior is a pure function of the recipe `(agent_id, seed, input)`; the runner draws randomness only from the recipe-seeded RNG and never consults the wall clock for decisions (timestamps recorded, never used).
- The store is the source of truth. The API answers reads only from the event log and its projections — never from the runner, never from OTel.
- Durable first: `POST /v1/runs` persists the run as `pending` BEFORE spawning the execution task, and returns `202` only after the persist.
- Terminal states (`completed`, `failed`, `cancelled`) are immutable, enforced at the store layer. First terminal write wins.
- `cancelling` is a persisted status transition, not an in-memory flag; the in-memory signal to the task is an addition, never a substitute.
- `trace_id` is generated at run creation and stored; the root span is later started with that same `trace_id` so envelope and backend always agree.
- Event sequence numbers are per-run, monotonic, starting at 1.
- Event append + step projection + run totals update happen in ONE transaction.
- Each retry attempt is its own child span under its step span.
- Metrics are never labeled with `run_id` or `metadata.*` values — bounded labels only (`agent_id`, `status`, `direction`, `step_type`, `outcome`).
- HTTP status codes only via `starlette.status` constants; no secrets in the repo; every error response uses the shared error envelope.
