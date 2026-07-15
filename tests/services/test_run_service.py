import asyncio
from datetime import UTC, datetime

import pytest

from app.domain.errors import RunErrorCode
from app.domain.events import RunCancelling, RunCompleted, RunStarted
from app.domain.status import RunStatus
from app.persistence.errors import RunNotFoundError, TerminalRunConflictError
from app.persistence.sqlite_repository import SqliteRepository
from app.runner.execute import CancelSignal
from app.services.run_service import RunService

NOW = datetime(2026, 1, 1, tzinfo=UTC)


async def _create(repo: SqliteRepository, run_id: str = "run-1") -> None:
    await repo.create_run(
        run_id=run_id,
        agent_id="agent-simple",
        seed=1,
        input={"prompt": "x"},
        metadata=None,
        trace_id="a" * 32,
        created_at=NOW,
    )


# --- tail_events -------------------------------------------------------


async def test_tail_events_replays_historical_events_in_order(repo: SqliteRepository) -> None:
    await _create(repo)
    await repo.append_event(RunStarted(run_id="run-1", sequence=1, occurred_at=NOW))
    await repo.append_event(
        RunCompleted(
            run_id="run-1", sequence=1, occurred_at=NOW, tokens_in=0, tokens_out=0,
            cost_usd=0.0, duration_ms=1,
        )
    )

    service = RunService(repo, sim_speed=1000.0)
    events = [e async for e in service.tail_events("run-1", after_sequence=0)]
    assert [e.sequence for e in events if e is not None] == [1, 2, 3]
    assert events[-1] is not None
    assert events[-1].event_type == "run_completed"


async def test_tail_events_resumes_from_given_sequence(repo: SqliteRepository) -> None:
    await _create(repo)
    await repo.append_event(RunStarted(run_id="run-1", sequence=1, occurred_at=NOW))
    await repo.append_event(
        RunCompleted(
            run_id="run-1", sequence=1, occurred_at=NOW, tokens_in=0, tokens_out=0,
            cost_usd=0.0, duration_ms=1,
        )
    )

    service = RunService(repo, sim_speed=1000.0)
    events = [e async for e in service.tail_events("run-1", after_sequence=1)]
    assert [e.sequence for e in events if e is not None] == [2, 3]


async def test_tail_events_live_tails_new_events_via_the_bus(repo: SqliteRepository) -> None:
    await _create(repo)
    service = RunService(repo, sim_speed=1000.0)
    collected = []

    async def _consume() -> None:
        async for event in service.tail_events("run-1", after_sequence=0):
            if event is not None:
                collected.append(event)
                if event.event_type == "run_completed":
                    return

    task = asyncio.create_task(_consume())
    await asyncio.sleep(0.05)  # let the consumer subscribe before we publish

    started = await repo.append_event(RunStarted(run_id="run-1", sequence=1, occurred_at=NOW))
    service._event_bus.publish("run-1", started)  # type: ignore[attr-defined]
    completed = await repo.append_event(
        RunCompleted(
            run_id="run-1", sequence=1, occurred_at=NOW, tokens_in=1, tokens_out=2,
            cost_usd=0.01, duration_ms=5,
        )
    )
    service._event_bus.publish("run-1", completed)  # type: ignore[attr-defined]

    await asyncio.wait_for(task, timeout=2.0)
    assert [e.event_type for e in collected] == ["run_created", "run_started", "run_completed"]


async def test_tail_events_yields_heartbeat_when_idle(repo: SqliteRepository) -> None:
    await _create(repo)
    service = RunService(repo, sim_speed=1000.0)

    gen = service.tail_events("run-1", after_sequence=0, heartbeat_interval=0.05)
    try:
        first = await gen.__anext__()
        assert first is not None  # historical RunCreated

        second = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
        assert second is None  # heartbeat — nothing new was appended
    finally:
        await gen.aclose()


# --- cancel_run ----------------------------------------------------------


async def test_cancel_run_persists_cancelling_and_signals_the_task(repo: SqliteRepository) -> None:
    await _create(repo)
    await repo.append_event(RunStarted(run_id="run-1", sequence=1, occurred_at=NOW))

    service = RunService(repo, sim_speed=1000.0)
    signal = CancelSignal()
    service._cancel_signals["run-1"] = signal  # type: ignore[attr-defined]

    record = await service.cancel_run("run-1")
    assert record.status is RunStatus.CANCELLING
    assert signal.is_set()


async def test_cancel_run_on_terminal_run_raises_conflict(repo: SqliteRepository) -> None:
    await _create(repo)
    await repo.append_event(RunStarted(run_id="run-1", sequence=1, occurred_at=NOW))
    await repo.append_event(
        RunCompleted(
            run_id="run-1", sequence=1, occurred_at=NOW, tokens_in=0, tokens_out=0,
            cost_usd=0.0, duration_ms=1,
        )
    )
    service = RunService(repo, sim_speed=1000.0)
    with pytest.raises(TerminalRunConflictError):
        await service.cancel_run("run-1")


async def test_cancel_unknown_run_raises_not_found(repo: SqliteRepository) -> None:
    service = RunService(repo, sim_speed=1000.0)
    with pytest.raises(RunNotFoundError):
        await service.cancel_run("does-not-exist")


# --- _ensure_terminal (M5.T6's finally-block guarantee) ------------------


async def test_ensure_terminal_appends_run_cancelled_when_status_is_cancelling(
    repo: SqliteRepository,
) -> None:
    await _create(repo)
    await repo.append_event(RunStarted(run_id="run-1", sequence=1, occurred_at=NOW))
    await repo.append_event(RunCancelling(run_id="run-1", sequence=1, occurred_at=NOW))

    service = RunService(repo, sim_speed=1000.0)
    await service._ensure_terminal("run-1")  # type: ignore[attr-defined]

    record = await repo.get_run("run-1")
    assert record is not None
    assert record.status is RunStatus.CANCELLED


async def test_ensure_terminal_appends_run_failed_when_stopped_unexpectedly(
    repo: SqliteRepository,
) -> None:
    await _create(repo)
    await repo.append_event(RunStarted(run_id="run-1", sequence=1, occurred_at=NOW))

    service = RunService(repo, sim_speed=1000.0)
    await service._ensure_terminal("run-1")  # type: ignore[attr-defined]

    record = await repo.get_run("run-1")
    assert record is not None
    assert record.status is RunStatus.FAILED
    assert record.error is not None
    assert record.error.code is RunErrorCode.STEP_FAILED
    assert record.error.retryable is False


async def test_ensure_terminal_is_a_no_op_when_already_terminal(repo: SqliteRepository) -> None:
    await _create(repo)
    await repo.append_event(RunStarted(run_id="run-1", sequence=1, occurred_at=NOW))
    await repo.append_event(
        RunCompleted(
            run_id="run-1", sequence=1, occurred_at=NOW, tokens_in=5, tokens_out=5,
            cost_usd=0.1, duration_ms=1,
        )
    )

    service = RunService(repo, sim_speed=1000.0)
    await service._ensure_terminal("run-1")  # type: ignore[attr-defined]

    record = await repo.get_run("run-1")
    assert record is not None
    assert record.status is RunStatus.COMPLETED
    assert record.tokens_in == 5
