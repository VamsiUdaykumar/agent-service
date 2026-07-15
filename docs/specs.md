# PRD — Agent Runs API & Observability

StackAI take-home: a run-execution HTTP API, a seeded fake agent runner, end-to-end OpenTelemetry into a real backend, and a customer-actionable analytics view.

---

## 1. Project overview

A developer starts an **agent run** — a multi-step execution involving model calls, tool calls, and occasionally sub-agents — through a public, versioned HTTP API, follows it live to completion, and reads back what happened and what it cost. Behind the API sits a **seeded, deterministic fake runner**. Every step it executes is instrumented once and emitted to two sinks:

- an append-only **event log** in SQLite — the product's source of truth, served by the API as three projections (run envelope, steps, SSE stream), and
- **OpenTelemetry spans and metrics** — exported through a local Collector to Grafana Cloud, where a single run is legible end to end and aggregate dashboards answer customer questions.

Guiding sentence: **instrument once, serve three audiences** — the developer following a run (API + SSE), the operator investigating one (traces), and the customer acting on trends (analytics).

The design's north star is defensibility: every included feature has a stated reason for inclusion, every excluded feature a stated reason for exclusion, and every architectural choice names the alternative it beat and the seam where the production-scale version would slot in.

---

## 2. Core requirements

**Required baselines (from the brief):**

1. **API as a product** — a public, versioned HTTP surface an external developer could integrate against without asking questions, documented by a shippable OpenAPI spec.
2. **A trace you can investigate from** — one run is legible end to end in a real observability backend: what happened, where the time went, what it cost, why it failed.

**Chosen features (at least one required; we commit to two):**

3. **Long-running runs done right** — a run outlasts a single request; callers can follow it cleanly to completion, through disconnects and server restarts.
4. **Cost and token accounting** — per-run and aggregate numbers a customer could trust and act on.

*Why these two:* the brief already demands an answer to long-running runs, so doing it properly is cheap relative to its value; cost accounting compounds the required analytics baseline (they share one data model and one instrumentation effort). Together they cover both sides of the role — API/product design and observability.
*Why not the rest:* webhooks are the push counterpart of long-running runs (redundant with our pull design); rate limiting and audit trail presuppose multi-tenancy (a named non-goal) and demo poorly single-user; batch API is a loop over creation unless real batch semantics are built — scope without new signal.

**Code standards (non-negotiable, from the brief):**

- Python, FastAPI, Pydantic, fully async; typed throughout.
- API, service, and persistence layers kept separate (made checkable by the repo layout).
- No hardcoded HTTP status codes (use `starlette.status`) and no secrets in the repo.
- Real OpenTelemetry instrumentation; generated OpenAPI spec (served at `/docs`, committed as `openapi.json`).
- Seeded, reproducible runs; easy to start (two commands, no accounts required to run).

**Cross-cutting invariants:**

- Run behavior is a pure function of the recipe `(agent_id, seed, input)`; the runner never consults the wall clock for decisions.
- Terminal run states are immutable, enforced at the store layer.
- The store is the source of truth; the API never reads from OTel (one-way dependency).

---

## 3. Core features

### 3.1 Run lifecycle & long-running follow

- States: `pending → running → {completed, failed, cancelled}`, with a transient `cancelling`. Structured error object on failure; retries are step-scoped (a run stays `running` while a step retries).
- `POST /v1/runs` persists the run as `pending` **before** spawning execution and returns `202` + `Location` immediately — "durable first, execute second"; the response never lies.
- **Follow = poll + SSE with resume.** Polling (`GET /v1/runs/{id}`, `/steps`) is the durable source of truth; SSE (`GET /v1/runs/{id}/events`) is the live view. Every event carries a per-run sequence number; a reconnecting client sends `Last-Event-ID: N` and receives exactly N+1 onward, byte-identical to a fresh replay — trivially correct because the stream is a tail of the persisted log.
- **Restart recovery:** on startup, orphaned `pending`/`running` runs are resolved to `failed` (`interrupted_by_restart`) with a terminal event.
- **Cancellation** (optional extension, in scope): `POST /v1/runs/{id}/cancel` → `202` + `cancelling`; the runner stops at the next step boundary and a `finally` block guarantees the terminal event and closed root span. Cancel on a terminal run → `409`. First terminal write wins.

### 3.2 Cost & token accounting

- Every model-call step draws input/output tokens; cost = tokens × a static two-model price table. Failed attempts consume tokens (failure costs money — true to life and visible in analytics).
- Totals folded onto the run envelope (`tokens`, `cost_usd`); per-step detail on the steps projection and on spans (OTel GenAI semantic conventions); aggregates as metrics.
- Input vs. output tokens kept separate end to end (different prices, causes, and fixes; output typically 3–5× input price).

### 3.3 API as a product (contract details)

- **Create payload:** `agent_id` (required — selects a behavior profile), `input` (required, ≤32KB, part of the recipe), `seed` (optional — server-generated and returned when omitted, so every run ever created is replayable), `metadata` (optional string→string tag bag → list filters + span attributes).
- **Idempotency keys** (optional extension, in scope): `Idempotency-Key` header; first success stores key→run (24h); retries replay the original response; same key + different body → `409`; the concurrent race is decided by a `UNIQUE` constraint.
- **List endpoint:** cursor pagination (`{data, has_more, next_cursor}`); run IDs are time-sortable ULIDs so the ID is both identity and cursor; offset pagination rejected (pages shift under concurrent writes). Filters: `status`, `agent_id`, `metadata.<key>`, `created_after/before`; sort fixed at `created_at desc`.
- **Error model:** one envelope everywhere — `{type, code, message, param, request_id}`. `type` is a closed set (`invalid_request`, `not_found`, `conflict`, `idempotency_error`, `internal_error`); `code` is the growable specific case. FastAPI's default 422 handler is overridden to match. `request_id` on every response and on the trace — the support-ticket-to-trace link. 5xx bodies never leak internals. Validation errors list valid options where applicable — e.g. an unknown `agent_id` returns a message including `"Valid: agent-researcher, agent-simple, agent-flaky"` with `param: "agent_id"`.

### 3.4 Observability & analytics

- **One trace per run:** root `run` span; child spans per step; sub-agent steps nested one level deep; **each retry attempt is its own child span** so retry latency and backoff are visible as bars and gaps. GenAI semantic conventions + `stackai.*` namespace. Bidirectional linkage: `trace_id` on the envelope, `stackai.run.id` on spans.
- **Metrics (cardinality-disciplined — no `run_id` or `metadata.*` labels):** `runs.completed` (agent_id, status), `run.duration` histogram (agent_id), `tokens.used` (agent_id, direction), `cost.usd` (agent_id), `steps.executed` (step_type, outcome, agent_id). *Labels are for dimensions you graph side by side, not identities you look up.*
- **Dashboard — six panels, each implying an action:** cost over time by agent; output-token share by agent (ratio drift is the signal); run outcome rates by agent (failed runs still cost money); step failure/retry rate by step type (localizes problems to tools); duration p50/p95 with **exemplars** (click p95 → the actual slow trace); cost per completed run (unit economics).

### 3.5 Explicitly excluded / deferred

Webhooks, rate limiting, audit trail, batch API (rationale in §2). Deferred extensions, named with one-line costs so deferral reads as judgment: ETag/If-None-Match, generated SDK snippet, sampling strategy, alerting, endpoint deprecation, `/runs/{id}/replay` convenience endpoint (build only if time allows).

---

## 4. Core components

| Component | Responsibility | Key decisions |
|---|---|---|
| **API layer** (`app/api`) | Routers, request/response schemas, error handlers, SSE endpoint | Envelope schema shared between create and read; overridden 422 handler; status codes via constants |
| **Domain** (`app/domain`) | State machine, event types, agent profiles — pure, no I/O | Import-clean package = the checkable proof of layer separation; terminal-state immutability rules live here |
| **Services** (`app/services`) | Run service, executor, idempotency | Executor spawns `asyncio.Task`s; documented seam to a queue-backed worker (one line at the call site) |
| **Persistence** (`app/persistence`) | Repository interface + SQLite (`aiosqlite`) impl; append-only event log; eager projections | Event append + step row + run totals updated in one transaction; documented seam to Postgres (one adapter) |
| **Runner** (`app/runner`) | The fake agent: steps, latency, tokens, cost, failures, retries, sub-agents | Three profiles as one config dict (`agent-researcher` 5–8 steps/10% fail; `agent-simple` 2–3/2%; `agent-flaky` 4–6/35% + 5% non-retryable); all randomness from the recipe-seeded RNG; `SIM_SPEED` scales sleeps only, recorded durations stay simulated |
| **Telemetry** (`app/telemetry`) | OTel setup, span helpers, metric instruments | Spans are telemetry, the log is truth; nothing reads back from OTel |
| **OTel Collector** (container) | Receives OTLP from the app, forwards to Grafana Cloud | Production shape (app holds no vendor creds); the seam for sampling/second exporters; **debug-exporter fallback so the system runs fully without any account** |
| **Grafana Cloud** | Traces (Tempo), metrics, the analytics dashboard | Chosen over Honeycomb (weaker metrics story) and SigNoz (heavy compose stack vs. "easy to start"); OTLP endpoint is pure config, so any backend can be swapped in |

---

## 5. App / user flow

**Developer flow (the demo story):**

1. `POST /v1/runs` with `agent_id` + `input` (+ optional `seed`, `metadata`, `Idempotency-Key`) → `202` immediately, envelope with `status: pending`, the full recipe echoed, and `trace_id`.
2. Follow live: open `GET /v1/runs/{id}/events` (SSE) and watch `run.started`, `step.started`, `step.completed`… arrive as they happen. Kill the connection, reconnect with `Last-Event-ID` — missed events replay exactly.
3. Or poll: `GET /v1/runs/{id}` for status and running totals; `GET /v1/runs/{id}/steps` for per-step state (attempts, `last_error`, per-step cost).
4. Optionally cancel: `POST /v1/runs/{id}/cancel` → watch `cancelling → cancelled` on the stream.
5. Retry-safe create: resend the same request with the same `Idempotency-Key` → the same run comes back, no double spend.
6. Investigate: take `trace_id` from the envelope → open the waterfall in Grafana — sub-agent depth, a red failed attempt, backoff gaps, cost per span.
7. Act on trends: the dashboard — which agent drives spend, whose failures burn money, p95 outliers with exemplar click-through to the guilty trace.
8. Reproduce: re-POST a stored run's `(agent_id, seed, input)` (or `/replay` if built) → an identical run, step for step, laid side by side with the original.

**Internal data flow (per run):**

```
POST /v1/runs
  └─▶ persist run (pending)  ──▶ spawn asyncio task ──▶ return 202
                                       │
              Runner executes steps ───┤ per step, one action, two sinks:
                                       ├─▶ append event ──▶ SQLite event log (truth)
                                       └─▶ emit span   ──▶ Collector ──▶ Grafana
Reads (anytime, runner alive or not):
  GET /runs/{id}        ◀── run-level fold of the log
  GET /runs/{id}/steps  ◀── per-step fold of the log
  GET /runs/{id}/events ◀── the log, tailed (SSE, resumable)
```

The API answers reads purely from the log — it never queries the runner. That is what makes a run outlast any request, disconnect, or restart.

---

## 6. Techstack

| Layer | Choice | Note |
|---|---|---|
| Language / framework | Python 3.12+, FastAPI, Pydantic v2, pydantic-settings | Fully async, typed throughout |
| Persistence | SQLite via `aiosqlite`, repository pattern | Zero-setup; single-writer acceptable for one process; Postgres seam documented |
| Execution | `asyncio.Task` in-process + startup recovery | Queue/worker seam documented, not built |
| IDs | ULIDs | Time-sortable → the ID doubles as the pagination cursor |
| Streaming | Server-Sent Events with `Last-Event-ID` | 15s heartbeat comments against proxy buffering |
| Telemetry | OpenTelemetry SDK (traces + metrics), GenAI semantic conventions | In-memory exporter in tests — we test our instrumentation, not the vendor |
| Pipeline | OTel Collector (container), OTLP | Debug-exporter fallback when no cloud creds |
| Backend | Grafana Cloud (Tempo traces, metrics, dashboards, exemplars) | Free tier; optional local Jaeger container as hands-on hedge |
| Testing | pytest, pytest-asyncio, httpx `AsyncClient` | `SIM_SPEED=100` makes the suite fast |
| Packaging / DX | Docker Compose (api + collector), Makefile (`make demo`), `.env.example` | Two-command startup, no accounts required |

---

## 7. Implementation plan

Ordered so every phase ends runnable, and the riskiest integrations (SSE resume, OTel export) land early enough to fix. Phases 1–4 are the spine; 5–7 make it a product; 8 is the polish that gets graded.

**Phase 0 — Scaffold (½ hr).** Repo layout per §4, pydantic-settings config, compose file (api + collector with debug fallback), CI-less quality basics (ruff, mypy). *Done when:* `docker compose up` serves `/docs`.

**Phase 1 — Domain + persistence (2–3 hrs).** State machine with immutable terminals; event types; migrations; repository with the atomic append-event + update-projections transaction; ULID ids. *Done when:* state-machine and projection unit tests pass (tests #2).

**Phase 2 — Runner (2–3 hrs).** Profiles config, seeded RNG from the recipe, steps/latency/tokens/cost/failures/retries/sub-agents, `SIM_SPEED`. No HTTP yet — driven by a unit test. *Done when:* determinism test passes (test #1 — the headline claim).

**Phase 3 — Core API (2–3 hrs).** `POST /runs` (durable-first, 202 + Location), `GET /runs/{id}`, `/steps`, list with cursor pagination + filters, error envelope + overridden 422 handler, startup recovery. *Done when:* HTTP lifecycle + recovery tests pass (tests #3, #6, #7).

**Phase 4 — SSE + cancellation (2–3 hrs).** Event stream as log tail, `Last-Event-ID` resume, heartbeats; cancel endpoint with step-boundary semantics and 409-on-terminal. *Done when:* SSE-resume and cancel tests pass (tests #3, #4). **End of day 1 target: phases 0–4.**

**Phase 5 — Idempotency (1 hr).** Key table + UNIQUE constraint, replay path, mismatch 409, concurrent-race test (test #5).

**Phase 6 — Telemetry (2–3 hrs).** Root/step/attempt span tree, GenAI + `stackai.*` attributes, error statuses, the five metric instruments; collector wiring to Grafana Cloud; verify a real run end to end in Tempo. *Done when:* in-memory-exporter assertions pass and a live trace renders correctly.

**Phase 7 — Dashboard + demo data (2 hrs).** Six panels in Grafana, exemplar on the p95 panel; `make demo` seeding all three profiles with known seeds, one failure, one cancellation.

**Phase 8 — Product polish (2 hrs).** OpenAPI spec review + commit `openapi.json`; README (quickstart → demo → architecture diagram → one-line decisions → curl tour → screenshots); demo rehearsal against §5's flow, including the five defense questions (event log & projections, SSE resume, durable-first lifecycle, spans vs. events, metric cardinality).

**Stretch, only if phases 0–8 are green:** `/replay` endpoint with `replayed_from` span attribute; ETag on the envelope.
