# Defense questions

The five questions named in PRD §7 Phase 8, each answered with a pointer to
the specific code or decision that answers it. Written after rehearsing PRD
§5's full developer flow (steps 1–8) against the live `docker compose up`
stack (M9.T4.1) — every step worked as documented, no friction found; see
the README's curl tour, which is the exact transcript of that rehearsal.

## 1. How do the event log and its projections relate?

The `events` table (`app/persistence/sqlite_repository.py`, schema in
`001_init.sql`) is the only thing ever *written* as ground truth — one row
per domain event (`RunCreated`, `StepStarted`, `StepCompleted`, …), append-
only, `UNIQUE(run_id, sequence)`. The `runs` and `steps` tables are eager
**projections**: folds of that log, kept in sync in the same transaction as
the event write. `SqliteRepository.append_event` does all three — insert
the event row, update the `runs` row, update the relevant `steps` row — as
one atomic unit (M2.T3.2), so a projection can never observe a partial
event. Every read endpoint (`GET /v1/runs/{id}`, `/steps`, `/events`)
answers purely from these tables — never from the in-memory runner task,
never from OTel (`app/services/run_service.py`'s docstring states this
directly). That one-way dependency is what lets a run outlast the request
that created it, a disconnect, or a full process restart: the projections
are just a cached fold of a log that's still there regardless of what's
running in memory.

## 2. How does SSE resume actually work?

`GET /v1/runs/{id}/events` (`app/api/routes/runs.py::stream_events`) does
one thing: parse an optional `Last-Event-ID` header into `after_sequence`,
then call `RunService.tail_events(run_id, after_sequence)`. That method
reads `get_events_from(run_id, after_sequence)` from the repository —
literally "give me every event with `sequence > after_sequence`" — replays
those, then subscribes to an in-process pub/sub (`RunEventBus`,
`app/services/event_bus.py`) for anything appended after the historical
read. There is no separate "resume" code path; a byte-identical union with
a fresh full replay falls out for free because the live tail *is* a
continuation of the same historical read, parameterized only by where it
starts. Event sequence numbers are 1-indexed per run (amendment 3 in
`docs/todo.md`), so an absent header or `Last-Event-ID: 0` both naturally
mean "from the beginning" — no special-casing needed. Verified live: `curl
-N .../events`, `Ctrl-C`, reconnect with `Last-Event-ID: 5` — resumed at
sequence 6, no gap, no duplicate (see README curl tour, run live above).

## 3. Why durable-first, and what does it actually buy you?

`RunService.create_run` → `_create_run` (`app/services/run_service.py`)
persists the run as `pending` via `self._repository.create_run(...)` and
only *afterward* calls `self._spawn_execution(...)`, which does
`asyncio.create_task`. The HTTP handler
(`app/api/routes/runs.py::create_run`) returns the `202` built from that
already-persisted record. So by the time a caller sees `202`, the row
exists in SQLite — full stop, regardless of whether the process is killed
one instruction later. The alternative (spawn the task, then persist once
it starts) has a crash window where the API claimed a run exists that the
store has no record of — unrecoverable, and undetectable by the client.
Durable-first turns "did my run actually start?" into a question the store
alone can answer. It also underwrites startup recovery
(`app/services/recovery.py::recover_orphaned_runs`, wired into
`app/main.py`'s lifespan): any `pending`/`running`/`cancelling` row found at
boot is provably a real run that got orphaned by a prior crash (it could
never have been left in that state any other way), so resolving it to
`failed` (`interrupted_by_restart`) is safe and complete.

## 4. What's the difference between what a span records and what an event records?

An **event** (`app/domain/events.py`) is the product's ground truth: exactly
enough structured data to answer "what happened and what did it cost" —
persisted once, replayed forever, driving both the API and the projections.
A **span** (`app/telemetry/spans.py`, `app/telemetry/run_tracer.py`) is a
*view* of the same execution built for a different audience — the operator
staring at a waterfall trying to find where the time went. `RunTracer.on_event`
is the single point where every yielded event turns into exactly one span
action: `RunStarted` opens the root span *with the run's already-persisted
`trace_id`* (`start_run_span`, constructed via an explicit `SpanContext` so
OTel adopts the stored ID instead of minting its own — `app/telemetry/spans.py`
`start_run_span`, wired at `app/telemetry/run_tracer.py:52`); `StepStarted`
opens a step span, and critically, **each retry attempt gets its own child
span** (`start_attempt_span`) so backoff shows as a visible gap between
bars, not as inflated latency on one span. Spans never feed back into the
store — the run's `RunEnvelope.trace_id` is set once at creation
(`RunService._create_run`, before the runner even starts) and never updated
from anything OTel produces; `M7.T3.4`'s test asserts the *span's* actual
trace ID equals the *already-persisted* one, proving the adoption technique
works without ever reading a span back into truth. Concretely: the event
log can tell you a step retried twice and failed non-retryably; the trace
additionally tells you the first attempt was slow because of the model
call, not the tool call, because attempt spans are siblings, not merged.

## 5. Why are metrics cardinality-disciplined, and how is that enforced?

The five instruments (`app/telemetry/metrics.py::Metrics`) label with only
`agent_id`, `status`, `direction`, `step_type`, `outcome` — never `run_id`,
never `metadata.*`. This isn't a convention someone has to remember: each
`record_*` method's signature only accepts the bounded label set it needs
(`record_cost(self, *, agent_id, cost_usd)` — there's no parameter to leak a
run ID into even by accident), and `M7.T4.2` has a dedicated unit test
asserting no call site passes a forbidden label. The reason it matters: a
metric labeled by `run_id` creates one new time series *per run*, forever —
fine at demo scale, a cardinality explosion the moment this sees real
traffic, and it's the kind of mistake that's invisible until a Prometheus
instance falls over months later. High-cardinality identity lookups (find
me *this* run's data) are exactly what the event log and trace ID already
answer; metrics exist for the orthogonal question — dimensions you graph
side by side (which agent, which step type, which outcome), not identities
you look up. The dashboard's six panels (`grafana/dashboard.json`) are all
built from `sum by (<bounded labels>)`-style aggregations for this reason —
they could never accidentally render a per-run breakdown even if someone
tried, because the label to do it with was never recorded.
