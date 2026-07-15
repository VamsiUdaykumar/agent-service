"""PRD §7 Phase 2's headline claim: two runs of the runner with identical
`(agent_id, seed, input)` produce byte-identical event sequences — step
count, types, tokens, costs, failures, retries, everything except
wall-clock timestamps.
"""

from app.domain.profiles import PROFILES
from app.runner.execute import execute_run

FAST = 50_000.0


async def _run(agent_id: str, seed: int, input: str) -> list[dict]:
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
    agent_id: str, seed: int, input: str = "do the thing"
) -> list[dict]:
    first = await _run(agent_id, seed, input)
    second = await _run(agent_id, seed, input)
    assert first == second
    assert len(first) > 1  # at minimum RunStarted + a terminal event
    return first


async def test_researcher_profile_is_deterministic() -> None:
    await _assert_deterministic("researcher", seed=0)


async def test_simple_profile_is_deterministic() -> None:
    await _assert_deterministic("simple", seed=0)


async def test_flaky_profile_is_deterministic() -> None:
    await _assert_deterministic("flaky", seed=0)


async def test_flaky_profile_non_retryable_failure_seed_is_deterministic() -> None:
    # Found by brute-force search: seed 0 (with the default input below)
    # exhausts flaky's retries and terminates the run as failed.
    events = await _assert_deterministic("flaky", seed=0)
    assert events[-1]["event_type"] == "run_failed"
    assert events[-1]["error"]["retryable"] is False


async def test_different_seeds_produce_different_event_sequences() -> None:
    a = await _run("researcher", 0, "do the thing")
    b = await _run("researcher", 1, "do the thing")
    assert a != b


async def test_different_input_produces_different_event_sequence_for_same_seed() -> None:
    a = await _run("researcher", 0, "task A")
    b = await _run("researcher", 0, "task B")
    assert a != b
