"""M6.T5: idempotent `POST /v1/runs` over the real HTTP surface."""

import asyncio

import httpx
from starlette import status


async def test_same_key_and_body_replays_the_same_run(client: httpx.AsyncClient) -> None:
    payload = {"agent_id": "agent-simple", "input": {"prompt": "x"}, "seed": 1}
    headers = {"Idempotency-Key": "key-1"}

    first = await client.post("/v1/runs", json=payload, headers=headers)
    second = await client.post("/v1/runs", json=payload, headers=headers)

    assert first.status_code == status.HTTP_202_ACCEPTED
    assert second.status_code == status.HTTP_202_ACCEPTED
    assert first.json()["id"] == second.json()["id"]

    list_resp = await client.get("/v1/runs")
    assert len(list_resp.json()["data"]) == 1


async def test_same_key_different_body_returns_409_idempotency_error(
    client: httpx.AsyncClient,
) -> None:
    headers = {"Idempotency-Key": "key-1"}
    await client.post(
        "/v1/runs",
        json={"agent_id": "agent-simple", "input": {"prompt": "x"}, "seed": 1},
        headers=headers,
    )
    response = await client.post(
        "/v1/runs",
        json={"agent_id": "agent-simple", "input": {"prompt": "different"}, "seed": 1},
        headers=headers,
    )

    assert response.status_code == status.HTTP_409_CONFLICT
    body = response.json()
    assert body["type"] == "idempotency_error"
    assert body["param"] == "Idempotency-Key"

    list_resp = await client.get("/v1/runs")
    assert len(list_resp.json()["data"]) == 1  # the conflicting call created nothing


async def test_no_idempotency_key_header_creates_separate_runs(client: httpx.AsyncClient) -> None:
    payload = {"agent_id": "agent-simple", "input": {"prompt": "x"}, "seed": 1}

    first = await client.post("/v1/runs", json=payload)
    second = await client.post("/v1/runs", json=payload)

    assert first.json()["id"] != second.json()["id"]


async def test_concurrent_requests_with_the_same_new_key_create_exactly_one_run(
    client: httpx.AsyncClient,
) -> None:
    payload = {"agent_id": "agent-simple", "input": {"prompt": "x"}, "seed": 1}
    headers = {"Idempotency-Key": "shared-key"}

    responses = await asyncio.gather(
        *(client.post("/v1/runs", json=payload, headers=headers) for _ in range(10))
    )

    assert all(r.status_code == status.HTTP_202_ACCEPTED for r in responses)
    run_ids = {r.json()["id"] for r in responses}
    assert len(run_ids) == 1

    list_resp = await client.get("/v1/runs")
    assert len(list_resp.json()["data"]) == 1
