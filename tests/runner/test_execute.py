import pytest

from app.domain.events import (
    RunCompleted,
    RunFailed,
    RunStarted,
    StepCompleted,
    StepFailed,
    StepStarted,
)
from app.domain.profiles import PROFILES
from app.domain.status import StepType
from app.runner.execute import CancelSignal, execute_run

FAST = 50_000.0  # SIM_SPEED — keep the test suite fast


async def _collect(agent_id: str, seed: int, input: str = "task", **kwargs):
    events = []
    async for event in execute_run(
        run_id="run-1",
        agent_id=agent_id,
        profile=PROFILES[agent_id],
        seed=seed,
        input=input,
        sim_speed=FAST,
        **kwargs,
    ):
        events.append(event)
    return events


async def test_event_stream_starts_with_run_started() -> None:
    events = await _collect("simple", 0)
    assert isinstance(events[0], RunStarted)


async def test_successful_run_ends_with_run_completed_and_matching_totals() -> None:
    events = await _collect("simple", 0)
    assert isinstance(events[-1], RunCompleted)

    step_completed = [e for e in events if isinstance(e, StepCompleted)]
    assert sum(e.tokens_in or 0 for e in step_completed) == events[-1].tokens_in
    assert sum(e.tokens_out or 0 for e in step_completed) == events[-1].tokens_out
    assert events[-1].cost_usd == pytest.approx(sum(e.cost_usd or 0 for e in step_completed))


async def test_failed_run_ends_with_run_failed_and_includes_failed_attempt_tokens() -> None:
    # Seed 3 with input "task" (the _collect default) exhausts flaky's retries.
    events = await _collect("flaky", 3)
    assert isinstance(events[-1], RunFailed)
    assert events[-1].error.retryable is False

    step_events = [e for e in events if isinstance(e, StepCompleted | StepFailed)]
    expected_tokens_in = sum(e.tokens_in or 0 for e in step_events)
    assert events[-1].tokens_in == expected_tokens_in


async def test_step_started_carries_incrementing_attempt_on_retry() -> None:
    # Seed 1 is known (checked in test_determinism.py) to retry at least one step.
    events = await _collect("researcher", 1)
    step_started = [e for e in events if isinstance(e, StepStarted) and e.step_id == "step-1"]
    assert [e.attempt for e in step_started] == list(range(1, len(step_started) + 1))


async def test_sub_agent_children_carry_parent_step_id() -> None:
    found = False
    for seed in range(15):
        events = await _collect("researcher", seed)
        sub_agent_ids = {
            e.step_id
            for e in events
            if isinstance(e, StepStarted) and e.step_type is StepType.SUB_AGENT
        }
        if not sub_agent_ids:
            continue
        found = True
        children = [
            e for e in events if isinstance(e, StepStarted) and e.parent_step_id in sub_agent_ids
        ]
        assert children
        for child in children:
            assert child.step_type is not StepType.SUB_AGENT
    assert found, "expected at least one sub_agent step across 15 seeds"


async def test_cancel_before_first_step_boundary_stops_after_run_started() -> None:
    signal = CancelSignal()
    signal.set()
    events = await _collect("researcher", 0, cancel_signal=signal)
    assert events == [events[0]]
    assert isinstance(events[0], RunStarted)


async def test_cancel_mid_run_stops_before_a_terminal_event() -> None:
    signal = CancelSignal()
    events = []
    async for event in execute_run(
        run_id="run-1",
        agent_id="researcher",
        profile=PROFILES["researcher"],
        seed=0,
        input="task",
        sim_speed=FAST,
        cancel_signal=signal,
    ):
        events.append(event)
        if isinstance(event, StepCompleted):
            signal.set()

    assert not any(isinstance(e, RunCompleted | RunFailed) for e in events)
    assert any(isinstance(e, StepCompleted) for e in events)
