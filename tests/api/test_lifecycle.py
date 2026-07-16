import asyncio

import httpx
import pytest
from starlette import status

POLL_INTERVAL = 0.01
POLL_TIMEOUT = 5.0


async def _poll_until_terminal(client: httpx.AsyncClient, run_id: str) -> dict:
    elapsed = 0.0
    while elapsed < POLL_TIMEOUT:
        response = await client.get(f"/v1/runs/{run_id}")
        data = response.json()
        if data["status"] in ("completed", "failed", "cancelled"):
            return data
        await asyncio.sleep(POLL_INTERVAL)
        elapsed += POLL_INTERVAL
    pytest.fail(f"run {run_id} did not reach a terminal state in time")


@pytest.mark.parametrize("agent_id", ["agent-researcher", "agent-simple", "agent-flaky"])
async def test_create_then_poll_to_completion(client: httpx.AsyncClient, agent_id: str) -> None:
    response = await client.post(
        "/v1/runs", json={"agent_id": agent_id, "input": {"prompt": "do the thing"}, "seed": 1}
    )
    assert response.status_code == status.HTTP_202_ACCEPTED
    body = response.json()
    assert response.headers["location"] == f"/v1/runs/{body['id']}"
    assert body["status"] == "pending"
    assert body["trace_id"]
    assert body["seed"] == 1
    assert body["agent_id"] == agent_id

    final = await _poll_until_terminal(client, body["id"])
    assert final["status"] in ("completed", "failed")

    steps_response = await client.get(f"/v1/runs/{body['id']}/steps")
    assert steps_response.status_code == status.HTTP_200_OK
    steps_body = steps_response.json()
    assert len(steps_body["data"]) >= 1
    assert steps_body["has_more"] is False
    assert steps_body["next_cursor"] is None


async def test_seed_is_server_generated_when_omitted(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/v1/runs", json={"agent_id": "agent-simple", "input": {"prompt": "x"}}
    )
    assert response.status_code == status.HTTP_202_ACCEPTED
    assert isinstance(response.json()["seed"], int)


async def test_same_recipe_reproduces_identical_totals(client: httpx.AsyncClient) -> None:
    payload = {"agent_id": "agent-simple", "input": {"prompt": "reproduce me"}, "seed": 42}
    first = await client.post("/v1/runs", json=payload)
    second = await client.post("/v1/runs", json=payload)

    final1 = await _poll_until_terminal(client, first.json()["id"])
    final2 = await _poll_until_terminal(client, second.json()["id"])

    assert final1["status"] == final2["status"]
    assert final1["tokens_in"] == final2["tokens_in"]
    assert final1["tokens_out"] == final2["tokens_out"]
    assert final1["cost_usd"] == final2["cost_usd"]
