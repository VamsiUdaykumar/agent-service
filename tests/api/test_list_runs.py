import httpx
from starlette import status


async def _create(
    client: httpx.AsyncClient,
    *,
    agent_id: str = "agent-simple",
    seed: int,
    metadata: dict | None = None,
) -> str:
    response = await client.post(
        "/v1/runs",
        json={
            "agent_id": agent_id,
            "input": {"prompt": f"task-{seed}"},
            "seed": seed,
            "metadata": metadata,
        },
    )
    return str(response.json()["id"])


async def test_list_runs_pagination_covers_all_without_duplicates(
    client: httpx.AsyncClient,
) -> None:
    created = {await _create(client, seed=i) for i in range(5)}

    seen: set[str] = set()
    cursor = None
    for _ in range(10):
        params = {"limit": 2, **({"cursor": cursor} if cursor else {})}
        response = await client.get("/v1/runs", params=params)
        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        seen.update(run["id"] for run in body["data"])
        if not body["has_more"]:
            assert body["next_cursor"] is None
            break
        cursor = body["next_cursor"]
    else:
        raise AssertionError("pagination did not terminate")

    assert seen == created


async def test_list_runs_filters_by_agent_id(client: httpx.AsyncClient) -> None:
    await _create(client, agent_id="agent-simple", seed=1)
    await _create(client, agent_id="agent-researcher", seed=2)

    response = await client.get("/v1/runs", params={"agent_id": "agent-researcher"})
    body = response.json()
    assert len(body["data"]) == 1
    assert body["data"][0]["agent_id"] == "agent-researcher"


async def test_list_runs_filters_by_metadata_key(client: httpx.AsyncClient) -> None:
    await _create(client, seed=1, metadata={"team": "growth"})
    await _create(client, seed=2, metadata={"team": "core"})

    response = await client.get("/v1/runs", params={"metadata.team": "growth"})
    body = response.json()
    assert len(body["data"]) == 1
    assert body["data"][0]["metadata"] == {"team": "growth"}


async def test_list_runs_default_sort_is_created_at_desc(client: httpx.AsyncClient) -> None:
    first_id = await _create(client, seed=1)
    second_id = await _create(client, seed=2)

    response = await client.get("/v1/runs")
    ids = [run["id"] for run in response.json()["data"]]
    assert ids.index(second_id) < ids.index(first_id)
