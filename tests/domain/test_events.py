from datetime import UTC, datetime

import pytest

from app.domain.errors import RunError, RunErrorCode
from app.domain.events import (
    EVENT_TYPES,
    RunCancelled,
    RunCompleted,
    RunCreated,
    RunFailed,
    RunStarted,
    StepCompleted,
    StepFailed,
    StepRetried,
    StepStarted,
    parse_event,
)
from app.domain.status import StepType

NOW = datetime(2026, 1, 1, tzinfo=UTC)
ERROR = RunError(code=RunErrorCode.STEP_FAILED, message="boom", retryable=True)

INSTANCES = [
    RunCreated(
        run_id="r1",
        sequence=1,
        occurred_at=NOW,
        agent_id="researcher",
        seed=42,
        input="do the thing",
        metadata={"team": "growth"},
        trace_id="a" * 32,
    ),
    RunStarted(run_id="r1", sequence=2, occurred_at=NOW),
    StepStarted(
        run_id="r1",
        sequence=3,
        occurred_at=NOW,
        step_id="s1",
        step_type=StepType.MODEL_CALL,
        attempt=1,
        parent_step_id=None,
    ),
    StepCompleted(
        run_id="r1",
        sequence=4,
        occurred_at=NOW,
        step_id="s1",
        attempt=1,
        tokens_in=100,
        tokens_out=50,
        cost_usd=0.01,
        duration_ms=250,
    ),
    StepFailed(
        run_id="r1",
        sequence=5,
        occurred_at=NOW,
        step_id="s2",
        attempt=1,
        error=ERROR,
        duration_ms=100,
        tokens_in=80,
        tokens_out=0,
        cost_usd=0.002,
    ),
    StepRetried(
        run_id="r1",
        sequence=6,
        occurred_at=NOW,
        step_id="s2",
        next_attempt=2,
        delay_ms=500,
    ),
    RunCompleted(
        run_id="r1", sequence=7, occurred_at=NOW, tokens_in=100, tokens_out=50,
        cost_usd=0.01, duration_ms=1000,
    ),
    RunFailed(
        run_id="r1", sequence=7, occurred_at=NOW, error=ERROR, tokens_in=100,
        tokens_out=50, cost_usd=0.01, duration_ms=1000,
    ),
    RunCancelled(
        run_id="r1", sequence=7, occurred_at=NOW, tokens_in=100, tokens_out=50,
        cost_usd=0.01, duration_ms=1000,
    ),
]


@pytest.mark.parametrize("event", INSTANCES, ids=lambda e: e.event_type)
def test_event_round_trips_through_json(event: object) -> None:
    dumped = event.model_dump(mode="json")  # type: ignore[attr-defined]
    restored = parse_event(dumped)
    assert restored == event


def test_every_registered_type_covered_by_a_round_trip_instance() -> None:
    covered = {event.event_type for event in INSTANCES}  # type: ignore[attr-defined]
    assert covered == set(EVENT_TYPES)


def test_sequence_must_be_at_least_one() -> None:
    with pytest.raises(ValueError):
        RunStarted(run_id="r1", sequence=0, occurred_at=NOW)


def test_unknown_event_type_rejected() -> None:
    with pytest.raises(ValueError):
        parse_event(
            {
                "event_type": "not_a_real_event",
                "run_id": "r1",
                "sequence": 1,
                "occurred_at": NOW.isoformat(),
            }
        )
