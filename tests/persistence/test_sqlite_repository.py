import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.errors import RunError, RunErrorCode
from app.domain.events import (
    RunCancelled,
    RunCancelling,
    RunCompleted,
    RunStarted,
    StepCompleted,
    StepFailed,
    StepRetried,
    StepStarted,
)
from app.domain.status import RunStatus, StepStatus, StepType
from app.persistence.errors import (
    IllegalTransitionError,
    RunNotFoundError,
    TerminalRunConflictError,
)
from app.persistence.sqlite_repository import SqliteRepository

NOW = datetime(2026, 1, 1, tzinfo=UTC)


async def _create(repo: SqliteRepository, run_id: str = "run-1", **overrides: object) -> None:
    defaults: dict[str, object] = dict(
        run_id=run_id,
        agent_id="agent-researcher",
        seed=1,
        input={"prompt": "do the thing"},
        metadata={"team": "growth"},
        trace_id="a" * 32,
        created_at=NOW,
    )
    defaults.update(overrides)
    await repo.create_run(**defaults)  # type: ignore[arg-type]


async def test_create_run_persists_pending_row_and_first_event(repo: SqliteRepository) -> None:
    record = await _create_and_return(repo)
    assert record.status is RunStatus.PENDING
    assert record.tokens_in == 0
    assert record.cost_usd == 0.0

    events = await repo.get_events_from("run-1", after_sequence=0)
    assert len(events) == 1
    assert events[0].sequence == 1
    assert events[0].event_type == "run_created"


async def _create_and_return(repo: SqliteRepository, run_id: str = "run-1", **overrides: object):
    await _create(repo, run_id=run_id, **overrides)
    record = await repo.get_run(run_id)
    assert record is not None
    return record


async def test_full_lifecycle_projection_folding(repo: SqliteRepository) -> None:
    await _create(repo)
    await repo.append_event(RunStarted(run_id="run-1", sequence=1, occurred_at=NOW))
    await repo.append_event(
        StepStarted(
            run_id="run-1",
            sequence=1,
            occurred_at=NOW,
            step_id="s1",
            step_type=StepType.MODEL_CALL,
            attempt=1,
        )
    )
    await repo.append_event(
        StepCompleted(
            run_id="run-1",
            sequence=1,
            occurred_at=NOW,
            step_id="s1",
            attempt=1,
            tokens_in=100,
            tokens_out=50,
            cost_usd=0.01,
            duration_ms=200,
        )
    )
    await repo.append_event(
        RunCompleted(
            run_id="run-1",
            sequence=1,
            occurred_at=NOW,
            tokens_in=100,
            tokens_out=50,
            cost_usd=0.01,
            duration_ms=500,
        )
    )

    run = await repo.get_run("run-1")
    assert run is not None
    assert run.status is RunStatus.COMPLETED
    assert run.tokens_in == 100
    assert run.tokens_out == 50
    assert run.cost_usd == pytest.approx(0.01)

    steps = await repo.get_steps("run-1")
    assert len(steps) == 1
    assert steps[0].status is StepStatus.COMPLETED
    assert steps[0].tokens_in == 100
    assert steps[0].cost_usd == pytest.approx(0.01)

    events = await repo.get_events_from("run-1", after_sequence=0)
    assert [event.sequence for event in events] == [1, 2, 3, 4, 5]
    assert [event.event_type for event in events] == [
        "run_created",
        "run_started",
        "step_started",
        "step_completed",
        "run_completed",
    ]


async def test_sequence_numbers_are_1_indexed_and_monotonic(repo: SqliteRepository) -> None:
    await _create(repo)
    e1 = await repo.append_event(RunStarted(run_id="run-1", sequence=1, occurred_at=NOW))
    e2 = await repo.append_event(
        StepStarted(
            run_id="run-1",
            sequence=999,
            occurred_at=NOW,
            step_id="s1",
            step_type=StepType.TOOL_CALL,
            attempt=1,
        )
    )
    assert e1.sequence == 2  # RunCreated already claimed sequence 1
    assert e2.sequence == 3


async def test_failed_attempt_still_accumulates_tokens_and_cost(repo: SqliteRepository) -> None:
    await _create(repo)
    await repo.append_event(RunStarted(run_id="run-1", sequence=1, occurred_at=NOW))
    await repo.append_event(
        StepStarted(
            run_id="run-1",
            sequence=1,
            occurred_at=NOW,
            step_id="s1",
            step_type=StepType.MODEL_CALL,
            attempt=1,
        )
    )
    await repo.append_event(
        StepFailed(
            run_id="run-1",
            sequence=1,
            occurred_at=NOW,
            step_id="s1",
            attempt=1,
            error=RunError(code=RunErrorCode.STEP_FAILED, message="rate limited", retryable=True),
            duration_ms=120,
            tokens_in=40,
            tokens_out=0,
            cost_usd=0.004,
        )
    )

    run = await repo.get_run("run-1")
    assert run is not None
    assert run.tokens_in == 40
    assert run.cost_usd == pytest.approx(0.004)

    steps = await repo.get_steps("run-1")
    assert steps[0].status is StepStatus.FAILED
    assert steps[0].last_error is not None
    assert steps[0].last_error.code is RunErrorCode.STEP_FAILED
    assert steps[0].tokens_in == 40


async def test_step_retried_is_logged_but_does_not_change_step_status(
    repo: SqliteRepository,
) -> None:
    await _create(repo)
    await repo.append_event(RunStarted(run_id="run-1", sequence=1, occurred_at=NOW))
    await repo.append_event(
        StepStarted(
            run_id="run-1",
            sequence=1,
            occurred_at=NOW,
            step_id="s1",
            step_type=StepType.MODEL_CALL,
            attempt=1,
        )
    )
    await repo.append_event(
        StepFailed(
            run_id="run-1",
            sequence=1,
            occurred_at=NOW,
            step_id="s1",
            attempt=1,
            error=RunError(code=RunErrorCode.STEP_FAILED, message="timeout", retryable=True),
            duration_ms=50,
        )
    )
    retried = await repo.append_event(
        StepRetried(
            run_id="run-1", sequence=1, occurred_at=NOW, step_id="s1",
            next_attempt=2, delay_ms=100,
        )
    )
    assert retried.sequence == 5

    await repo.append_event(
        StepStarted(
            run_id="run-1",
            sequence=1,
            occurred_at=NOW,
            step_id="s1",
            step_type=StepType.MODEL_CALL,
            attempt=2,
        )
    )

    steps = await repo.get_steps("run-1")
    assert len(steps) == 1  # same step_id, upserted, not duplicated
    assert steps[0].status is StepStatus.RUNNING
    assert steps[0].attempt == 2


async def test_cancelling_is_a_persisted_status_transition(repo: SqliteRepository) -> None:
    await _create(repo)
    await repo.append_event(RunStarted(run_id="run-1", sequence=1, occurred_at=NOW))
    await repo.append_event(RunCancelling(run_id="run-1", sequence=1, occurred_at=NOW))

    run = await repo.get_run("run-1")
    assert run is not None
    assert run.status is RunStatus.CANCELLING

    await repo.append_event(
        RunCancelled(
            run_id="run-1", sequence=1, occurred_at=NOW, tokens_in=0, tokens_out=0,
            cost_usd=0.0, duration_ms=10,
        )
    )
    run = await repo.get_run("run-1")
    assert run is not None
    assert run.status is RunStatus.CANCELLED


async def test_terminal_run_rejects_further_writes(repo: SqliteRepository) -> None:
    await _create(repo)
    await repo.append_event(RunStarted(run_id="run-1", sequence=1, occurred_at=NOW))
    await repo.append_event(
        RunCompleted(
            run_id="run-1", sequence=1, occurred_at=NOW, tokens_in=0, tokens_out=0,
            cost_usd=0.0, duration_ms=10,
        )
    )

    before = await repo.get_run("run-1")
    assert before is not None
    assert before.status is RunStatus.COMPLETED

    with pytest.raises(TerminalRunConflictError):
        await repo.append_event(
            RunCancelled(
                run_id="run-1", sequence=1, occurred_at=NOW, tokens_in=0, tokens_out=0,
                cost_usd=0.0, duration_ms=20,
            )
        )

    after = await repo.get_run("run-1")
    assert after == before  # rejected write left the row byte-for-byte unchanged

    events = await repo.get_events_from("run-1", after_sequence=0)
    assert len(events) == 3  # RunCreated, RunStarted, RunCompleted — no partial/extra event


async def test_illegal_transition_rejected(repo: SqliteRepository) -> None:
    await _create(repo)
    # pending -> completed is not a legal transition (must go through running).
    with pytest.raises(IllegalTransitionError):
        await repo.append_event(
            RunCompleted(
                run_id="run-1", sequence=1, occurred_at=NOW, tokens_in=0, tokens_out=0,
                cost_usd=0.0, duration_ms=10,
            )
        )

    run = await repo.get_run("run-1")
    assert run is not None
    assert run.status is RunStatus.PENDING

    events = await repo.get_events_from("run-1", after_sequence=0)
    assert len(events) == 1  # only RunCreated — the rejected write inserted nothing


async def test_append_event_to_unknown_run_raises(repo: SqliteRepository) -> None:
    with pytest.raises(RunNotFoundError):
        await repo.append_event(RunStarted(run_id="does-not-exist", sequence=1, occurred_at=NOW))


async def test_atomicity_on_simulated_mid_write_failure(
    repo: SqliteRepository, monkeypatch
) -> None:
    await _create(repo)

    async def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated failure mid-projection-fold")

    monkeypatch.setattr(repo, "_fold_projection", _boom)

    with pytest.raises(RuntimeError):
        await repo.append_event(RunStarted(run_id="run-1", sequence=1, occurred_at=NOW))

    run = await repo.get_run("run-1")
    assert run is not None
    assert run.status is RunStatus.PENDING  # rolled back, no partial projection update

    events = await repo.get_events_from("run-1", after_sequence=0)
    assert len(events) == 1  # the event insert was rolled back along with the fold


async def test_get_events_from_resumes_after_given_sequence(repo: SqliteRepository) -> None:
    await _create(repo)
    await repo.append_event(RunStarted(run_id="run-1", sequence=1, occurred_at=NOW))
    await repo.append_event(
        StepStarted(
            run_id="run-1", sequence=1, occurred_at=NOW, step_id="s1",
            step_type=StepType.MODEL_CALL, attempt=1,
        )
    )

    full = await repo.get_events_from("run-1", after_sequence=0)
    tail = await repo.get_events_from("run-1", after_sequence=1)
    assert [e.sequence for e in full] == [1, 2, 3]
    assert [e.sequence for e in tail] == [2, 3]
    assert tail == full[1:]


async def test_list_runs_cursor_pagination_covers_all_rows_without_duplicates(
    repo: SqliteRepository,
) -> None:
    for i in range(25):
        await _create(repo, run_id=f"run-{i:03d}", created_at=NOW + timedelta(seconds=i))

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(10):  # generous upper bound on page count
        page = await repo.list_runs(cursor=cursor, limit=10)
        seen.extend(record.id for record in page.data)
        if not page.has_more:
            assert page.next_cursor is None
            break
        cursor = page.next_cursor
    else:
        pytest.fail("pagination did not terminate")

    assert len(seen) == len(set(seen)) == 25


async def test_list_runs_concurrent_inserts_produce_stable_pages(repo: SqliteRepository) -> None:
    async def _make(i: int) -> None:
        await _create(repo, run_id=f"conc-{i:03d}", created_at=NOW + timedelta(seconds=i))

    await asyncio.gather(*(_make(i) for i in range(20)))

    seen: list[str] = []
    cursor: str | None = None
    while True:
        page = await repo.list_runs(cursor=cursor, limit=7)
        seen.extend(record.id for record in page.data)
        if not page.has_more:
            break
        cursor = page.next_cursor

    assert len(seen) == len(set(seen)) == 20


async def test_list_runs_filters_by_status_agent_id_and_metadata(repo: SqliteRepository) -> None:
    await _create(
        repo, run_id="r-researcher", agent_id="agent-researcher", metadata={"team": "growth"}
    )
    await _create(repo, run_id="r-simple", agent_id="agent-simple", metadata={"team": "core"})
    await repo.append_event(RunStarted(run_id="r-simple", sequence=1, occurred_at=NOW))

    by_agent = await repo.list_runs(agent_id="agent-researcher")
    assert [r.id for r in by_agent.data] == ["r-researcher"]

    by_status = await repo.list_runs(status=RunStatus.RUNNING)
    assert [r.id for r in by_status.data] == ["r-simple"]

    by_metadata = await repo.list_runs(metadata_filters={"team": "core"})
    assert [r.id for r in by_metadata.data] == ["r-simple"]


async def test_list_runs_filters_by_created_after_and_before(repo: SqliteRepository) -> None:
    await _create(repo, run_id="early", created_at=NOW)
    await _create(repo, run_id="late", created_at=NOW + timedelta(hours=1))

    after = await repo.list_runs(created_after=NOW + timedelta(minutes=1))
    assert [r.id for r in after.data] == ["late"]

    before = await repo.list_runs(created_before=NOW + timedelta(minutes=1))
    assert [r.id for r in before.data] == ["early"]
