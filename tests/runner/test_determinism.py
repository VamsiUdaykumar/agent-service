"""PRD §7 Phase 2's headline claim: two runs of the runner with identical
`(agent_id, seed, input)` produce byte-identical event sequences — step
count, types, tokens, costs, failures, retries, everything except
wall-clock timestamps.
"""

from typing import Any

from app.domain.profiles import PROFILES
from app.runner.execute import execute_run

FAST = 50_000.0


async def _run(agent_id: str, seed: int, input: dict[str, Any]) -> list[dict]:
    events = []
    async for event in execute_run(
        run_id="run-1",
        agent_id=agent_id,
        profile=PROFILES[agent_id],
        seed=seed,
        input=input,
        sim_speed=FAST,
    ):
        dumped = event.model_dump(mode="json")
        dumped.pop("occurred_at")
        events.append(dumped)
    return events


async def _assert_deterministic(
    agent_id: str, seed: int, input: dict[str, Any] | None = None
) -> list[dict]:
    resolved_input = input if input is not None else {"prompt": "do the thing"}
    first = await _run(agent_id, seed, resolved_input)
    second = await _run(agent_id, seed, resolved_input)
    assert first == second
    assert len(first) > 1  # at minimum RunStarted + a terminal event
    return first


async def test_researcher_profile_is_deterministic() -> None:
    await _assert_deterministic("agent-researcher", seed=0)


async def test_simple_profile_is_deterministic() -> None:
    await _assert_deterministic("agent-simple", seed=0)


async def test_flaky_profile_is_deterministic() -> None:
    await _assert_deterministic("agent-flaky", seed=0)


async def test_flaky_profile_non_retryable_failure_seed_is_deterministic() -> None:
    # Found by brute-force search: seed 1 (with the default input below)
    # exhausts flaky's retries and terminates the run as failed.
    events = await _assert_deterministic("agent-flaky", seed=1)
    assert events[-1]["event_type"] == "run_failed"
    assert events[-1]["error"]["retryable"] is False


async def test_different_seeds_produce_different_event_sequences() -> None:
    a = await _run("agent-researcher", 0, {"prompt": "do the thing"})
    b = await _run("agent-researcher", 1, {"prompt": "do the thing"})
    assert a != b


async def test_different_input_produces_different_event_sequence_for_same_seed() -> None:
    a = await _run("agent-researcher", 0, {"prompt": "task A"})
    b = await _run("agent-researcher", 0, {"prompt": "task B"})
    assert a != b


async def test_input_key_order_does_not_affect_event_sequence() -> None:
    a = await _run("agent-researcher", 0, {"a": 1, "b": 2})
    b = await _run("agent-researcher", 0, {"b": 2, "a": 1})
    assert a == b
