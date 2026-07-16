# Agent Runs API & Observability

A developer starts an **agent run** — a multi-step execution involving model
calls, tool calls, and occasionally sub-agents — through a public, versioned
HTTP API, follows it live to completion, and reads back what happened and
what it cost.

Behind the API sits a **seeded, deterministic fake runner**. Every step it
executes is instrumented once and emitted to two sinks: an append-only
**SQLite event log** (the source of truth, served back as three
projections — run envelope, steps, SSE stream) and **OpenTelemetry
spans/metrics**, exported through a local Collector to Grafana Cloud.

**Instrument once, serve three audiences** — the developer following a run
(API + SSE), the operator investigating one (traces), and the customer
acting on trends (the dashboard).

Full design rationale: [`docs/specs.md`](docs/specs.md) (locked PRD).
Implementation plan with task-level detail: [`docs/todo.md`](docs/todo.md).

---

## Quickstart

Two commands, no accounts required:

```bash
make up      # docker compose up --build — serves the API on :8000, /docs for OpenAPI
make demo    # seeds all three profiles, one guaranteed failure, one guaranteed cancellation
```

`make up` also starts an OTel Collector alongside the API. It always emits
traces/metrics to a `debug` exporter you can read straight from
`docker compose logs collector` — no external account needed to see the
telemetry working. Point it at Grafana Cloud by setting
`GRAFANA_CLOUD_OTLP_ENDPOINT` / `GRAFANA_CLOUD_OTLP_AUTH_HEADER` in `.env`
(see `.env.example`); everything else about the system is identical either
way.

Other targets: `make test` (pytest, `SIM_SPEED=100` so the suite runs in
milliseconds), `make lint` (ruff), `make typecheck` (mypy), `make openapi`
(regenerate the committed `openapi.json` after a route/schema change),
`make down`.

## `make demo` walkthrough

[`scripts/demo.py`](scripts/demo.py) drives the live stack over real HTTP,
exactly as an external developer would — it's the only Milestone-8+ code
that isn't exercised by pytest. It seeds:

| Run | Recipe | Outcome |
|---|---|---|
| `agent-researcher` | `seed=0` | completes cleanly (5–8 steps, some sub-agent nesting) |
| `agent-simple` | `seed=0` | completes cleanly (2–3 steps) |
| `agent-flaky` | `seed=1` | fails — a pinned non-retryable-failure seed, same one M3.T7's determinism test uses |
| `agent-researcher` | `seed=999` | cancelled ~0.25s after creation, mid-run |

All four seeds are hardcoded and known by construction, not luck: run
behavior is a pure function of `(agent_id, seed, input)` (the repo's core
invariant), so every seed above always produces the same outcome — verified
directly against the runner before being pinned into the script (see
`docs/todo.md` M8.T3.1).

After it finishes:

```bash
curl http://localhost:8000/v1/runs | jq
```

...and import [`grafana/dashboard.json`](grafana/dashboard.json) into
Grafana Cloud to see the six-panel analytics view populate.

## Architecture

```mermaid
flowchart TB
    subgraph client[Developer / integrator]
        C[HTTP client]
    end

    subgraph api_process[api container]
        API["API layer (app/api)<br/>routers, schemas, error envelope, SSE"]
        SVC["Services (app/services)<br/>RunService: durable-first create,<br/>asyncio.Task executor, idempotency"]
        DOM["Domain (app/domain)<br/>status machine, events, agent profiles<br/>— pure, zero I/O"]
        RUN["Runner (app/runner)<br/>seeded fake agent: steps, tokens,<br/>cost, failures, retries, sub-agents"]
        TEL["Telemetry (app/telemetry)<br/>span tree + metric instruments"]
        PER["Persistence (app/persistence)<br/>Repository protocol + SqliteRepository"]
    end

    DB[(SQLite<br/>events / runs / steps<br/>— source of truth)]
    COL["OTel Collector<br/>(container)"]
    GC["Grafana Cloud<br/>Tempo traces + Mimir metrics<br/>+ dashboard"]

    C -->|"POST /v1/runs, GET .../steps,<br/>GET .../events (SSE), POST .../cancel"| API
    API --> SVC
    SVC -->|"1: persist run (pending)"| PER
    SVC -->|"2: spawn asyncio.Task"| RUN
    RUN -->|"yields domain events"| SVC
    SVC -->|"append_event (one txn:<br/>event + run + step projections)"| PER
    PER --> DB
    SVC -->|"on_event: open/close spans,<br/>record metrics"| TEL
    TEL -->|OTLP| COL
    COL -->|"otlphttp (real creds)"| GC
    COL -->|"debug exporter<br/>(always on, zero accounts)"| LOGS["docker compose logs collector"]
    API -.->|"reads answer purely from<br/>the log — never the runner,<br/>never OTel"| PER
    DOM -.->|"pure spec, no I/O"| RUN
    DOM -.-> SVC

    style DB fill:#2b6cb0,color:#fff
    style GC fill:#e07000,color:#fff
```

| Component | Responsibility | Key decision |
|---|---|---|
| **API** (`app/api`) | Routers, schemas, error envelope, SSE | Envelope shared between create/read; overridden 422 handler so validation errors match the one `ErrorEnvelope` shape too |
| **Domain** (`app/domain`) | State machine, event types, agent profiles — pure, no I/O | Import-clean package is the checkable proof of layer separation |
| **Services** (`app/services`) | `RunService`: durable-first create, executor, idempotency | Executor spawns `asyncio.Task`; the queue-worker seam is a one-line comment at that call site |
| **Persistence** (`app/persistence`) | `Repository` protocol + `SqliteRepository`, append-only log, eager projections | Event append + step row + run totals in one transaction; Postgres seam is one adapter away |
| **Runner** (`app/runner`) | The fake agent: steps, latency, tokens, cost, failures, retries, sub-agents | All randomness from the recipe-seeded RNG; `SIM_SPEED` scales sleeps only, never recorded durations |
| **Telemetry** (`app/telemetry`) | OTel setup, span helpers, metric instruments | Spans are telemetry, the log is truth — nothing reads back from OTel |
| **OTel Collector** | Receives OTLP, forwards to Grafana Cloud | `debug` exporter always on, so the system runs fully without any account |
| **Grafana Cloud** | Tempo traces, Mimir metrics, the dashboard | OTLP endpoint is pure config — any backend can be swapped in |

## Decisions (the alternative each one beat)

- **Durable-first execution** — `POST /v1/runs` persists `pending` *before*
  spawning the run, so the `202` never lies. Beats "execute then persist,"
  which can claim a run exists when a crash before the write would leave it
  invisible.
- **`cancelling` is a persisted status, not an in-memory flag** — a crashed
  and restarted process can still observe and finish a cancellation from the
  store alone. Beats an in-memory-only signal, which loses the cancel
  request across a restart.
- **SQLite via `aiosqlite`, repository pattern** — zero-setup, single-writer
  is fine for one process; the `Repository` protocol is the documented seam
  to swap in Postgres later without touching call sites.
- **ULIDs as run IDs** — time-sortable, so the ID doubles as the pagination
  cursor. Beats UUID4 + offset pagination, which is both less informative
  and shifts pages under concurrent writes (explicitly rejected, PRD §3.3).
- **`asyncio.Task` in-process executor** — beats standing up a real queue
  for this scope; the enqueue seam is a documented one-line swap.
- **SSE with `Last-Event-ID` resume** — beats WebSockets for a one-way
  follow: no bidirectional complexity, and correctness falls straight out of
  "the stream is just a tail of the persisted log."
- **Idempotency via a `UNIQUE` DB constraint** — the concurrent-race case
  (two requests, same new key) is decided by the database, not app-level
  locking; the loser catches the constraint violation and replays instead of
  erroring.
- **Cardinality-disciplined metrics** — no `run_id` or `metadata.*` labels,
  ever (enforced by each `record_*` method's signature, not convention).
  Beats the naive version, which would blow up cardinality the moment this
  ran against real traffic.
- **OTel Collector with a `debug`-exporter fallback** — the app never holds
  vendor credentials (production shape), and the system runs fully with zero
  accounts if Grafana Cloud creds are unset.
- **Grafana Cloud** over Honeycomb (weaker metrics story) and SigNoz (a
  heavier compose stack than "two commands, no accounts" wants).
- **Cost/count dashboard panels read raw cumulative counters, not
  `rate()`/`increase()`** — `make demo` produces a one-off burst, not
  continuous traffic; windowed rate functions extrapolate over so few
  samples that a true value of `1` can render as `0` (see `docs/todo.md`
  M9.T6.3). The duration histogram panel is the one exception —
  `histogram_quantile` genuinely needs `increase()` to compute a quantile
  from bucket counts.

## Curl tour

```bash
# 1. Start a run — 202 + Location header + the pending envelope, trace_id already set
curl -i -X POST http://localhost:8000/v1/runs \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "agent-researcher", "input": {"prompt": "summarize the attached document"}, "seed": 42}'
# -> 202 Accepted, Location: /v1/runs/01J...
# {"id": "01J...", "status": "pending", "trace_id": "9a15...", ...}

RUN_ID=01J...   # from the response above

# 2. Poll for status/totals
curl http://localhost:8000/v1/runs/$RUN_ID

# 2b. Per-step detail
curl http://localhost:8000/v1/runs/$RUN_ID/steps

# 3. Follow live via SSE — Ctrl-C mid-stream, then resume with Last-Event-ID
curl -N http://localhost:8000/v1/runs/$RUN_ID/events
curl -N -H "Last-Event-ID: 3" http://localhost:8000/v1/runs/$RUN_ID/events   # resumes from sequence 4

# 4. Cancel a run (409 if it's already terminal)
curl -i -X POST http://localhost:8000/v1/runs/$RUN_ID/cancel

# 5. Retry-safe create — same key + same body replays the same run, no double execution
curl -i -X POST http://localhost:8000/v1/runs \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: demo-key-1" \
  -d '{"agent_id": "agent-simple", "input": {"prompt": "x"}}'
curl -i -X POST http://localhost:8000/v1/runs \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: demo-key-1" \
  -d '{"agent_id": "agent-simple", "input": {"prompt": "x"}}'
# -> identical response both times, same "id"
```

Full OpenAPI spec: [`/docs`](http://localhost:8000/docs) while the stack is
running, or the committed [`openapi.json`](openapi.json).

## Screenshots

_(Added after a live `make demo` + Grafana Cloud run — see
[`docs/images/README.md`](docs/images/README.md) for what each should show.)_

**Trace waterfall — `agent-researcher`:** root span, per-step children,
sub-agent nesting.

![researcher waterfall](docs/images/researcher-waterfall.png)

**Trace waterfall — `agent-flaky` retry:** each retry attempt as its own
child span, backoff visible as a gap.

![flaky retry trace](docs/images/flaky-retry-trace.png)

**Dashboard:** the six analytics panels with `make demo` data loaded.

![dashboard](docs/images/dashboard.png)

## Defense questions

See [`docs/defense-questions.md`](docs/defense-questions.md) for the five
questions named in PRD §7 Phase 8 (event log & projections, SSE resume,
durable-first lifecycle, spans vs. events, metric cardinality), each
answered with a pointer to the specific code/decision.
