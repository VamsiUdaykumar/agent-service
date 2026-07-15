"""`make demo` (M8.T3): seeds the live `docker compose up` stack with one
clean run per profile, one guaranteed non-retryable failure, and one
guaranteed cancellation, so the dashboard (grafana/dashboard.json) and the
demo flow (PRD §5) always have interesting data to show.

Seeds are hardcoded and known by construction, not luck: the runner is a
pure function of (agent_id, seed, input) (the repo's core invariant), so
each recipe below always produces the same outcome. They were verified by
directly driving `app.runner.execute.execute_run` with SIM_SPEED scaled up —
see docs/todo.md M8.T3.1.

Talks to the API over real HTTP, exactly as an external developer would —
this is the only Milestone-8 code that isn't exercised by pytest.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from typing import Any

import httpx

API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000")

_POLL_INTERVAL_S = 0.5
_POLL_TIMEOUT_S = 45.0
_HEALTH_RETRY_S = 0.5
_HEALTH_RETRIES = 20
_CANCEL_DELAY_S = 0.25

# agent-researcher/agent-simple seed=0 both complete cleanly with this input
# (brute-force verified across seeds 0-9, M8.T3.1). agent-flaky seed=1 is the
# same non-retryable-failure seed M3.T7's determinism test pins down.
_KNOWN_RUNS: list[dict[str, Any]] = [
    {"agent_id": "agent-researcher", "seed": 0, "input": {"prompt": "do the thing"}},
    {"agent_id": "agent-simple", "seed": 0, "input": {"prompt": "do the thing"}},
    {"agent_id": "agent-flaky", "seed": 1, "input": {"prompt": "do the thing"}},
]

# agent-researcher seed=999's step plan opens with a model_call step
# (300-2500ms simulated latency), so cancelling _CANCEL_DELAY_S after create
# always lands while the run is still non-terminal.
_CANCEL_RUN: dict[str, Any] = {
    "agent_id": "agent-researcher",
    "seed": 999,
    "input": {"prompt": "cancel me"},
}


async def _wait_for_api(client: httpx.AsyncClient) -> None:
    for _ in range(_HEALTH_RETRIES):
        try:
            response = await client.get("/health")
            if response.status_code == 200:
                return
        except httpx.TransportError:
            pass
        await asyncio.sleep(_HEALTH_RETRY_S)
    raise SystemExit(
        f"agent-service API not reachable at {API_BASE_URL} — run `docker compose up` first"
    )


async def _create_run(client: httpx.AsyncClient, spec: dict[str, Any]) -> dict[str, Any]:
    response = await client.post(
        "/v1/runs",
        json={"agent_id": spec["agent_id"], "input": spec["input"], "seed": spec["seed"]},
    )
    response.raise_for_status()
    envelope: dict[str, Any] = response.json()
    return envelope


async def _poll_until_terminal(client: httpx.AsyncClient, run_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + _POLL_TIMEOUT_S
    while True:
        response = await client.get(f"/v1/runs/{run_id}")
        response.raise_for_status()
        record: dict[str, Any] = response.json()
        if record["status"] in ("completed", "failed", "cancelled"):
            return record
        if time.monotonic() > deadline:
            raise SystemExit(
                f"run {run_id} did not reach a terminal state within {_POLL_TIMEOUT_S}s"
            )
        await asyncio.sleep(_POLL_INTERVAL_S)


async def _seed_known_runs(client: httpx.AsyncClient) -> None:
    for spec in _KNOWN_RUNS:
        envelope = await _create_run(client, spec)
        print(f"created {envelope['id']}  ({spec['agent_id']}, seed={spec['seed']}) -> pending")
        final = await _poll_until_terminal(client, envelope["id"])
        print(
            f"  -> {final['status']:<10} trace_id={final['trace_id']}  "
            f"cost_usd={final['cost_usd']}"
        )


async def _seed_cancelled_run(client: httpx.AsyncClient) -> None:
    envelope = await _create_run(client, _CANCEL_RUN)
    print(
        f"created {envelope['id']}  (agent-researcher, seed=999) -> pending, "
        f"cancelling in {_CANCEL_DELAY_S}s..."
    )
    await asyncio.sleep(_CANCEL_DELAY_S)

    response = await client.post(f"/v1/runs/{envelope['id']}/cancel")
    response.raise_for_status()

    final = await _poll_until_terminal(client, envelope["id"])
    print(f"  -> {final['status']:<10} trace_id={final['trace_id']}")
    if final["status"] != "cancelled":
        print(
            f"  note: run reached {final['status']!r} before the cancel checkpoint tripped "
            "(cancelling -> completed is a legal race, see app/domain/status.py)",
            file=sys.stderr,
        )


async def main() -> None:
    async with httpx.AsyncClient(base_url=API_BASE_URL, timeout=10.0) as client:
        await _wait_for_api(client)
        print(f"seeding demo data against {API_BASE_URL} ...\n")

        await _seed_known_runs(client)
        await _seed_cancelled_run(client)

        print("\ndemo data seeded. Explore it:")
        print(f"  curl {API_BASE_URL}/v1/runs | jq")
        print("  Grafana Cloud: import grafana/dashboard.json to see it on the six-panel dashboard")


if __name__ == "__main__":
    asyncio.run(main())
