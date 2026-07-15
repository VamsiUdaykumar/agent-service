import httpx
from starlette import status


async def test_404_on_unknown_run(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/runs/does-not-exist")
    assert response.status_code == status.HTTP_404_NOT_FOUND
    body = response.json()
    assert body["type"] == "not_found"
    assert body["code"] == "run_not_found"
    assert body["request_id"]


async def test_404_on_unknown_run_steps(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/runs/does-not-exist/steps")
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["type"] == "not_found"


async def test_422_on_missing_agent_id(client: httpx.AsyncClient) -> None:
    response = await client.post("/v1/runs", json={"input": {"prompt": "x"}})
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    body = response.json()
    assert body["type"] == "invalid_request"
    assert body["param"] == "body.agent_id"


async def test_422_on_non_object_input(client: httpx.AsyncClient) -> None:
    response = await client.post("/v1/runs", json={"agent_id": "agent-simple", "input": "x"})
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    body = response.json()
    assert body["type"] == "invalid_request"
    assert body["param"] == "body.input"


async def test_422_on_oversized_input(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/v1/runs", json={"agent_id": "agent-simple", "input": {"prompt": "x" * 40_000}}
    )
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json()["type"] == "invalid_request"


async def test_422_on_unknown_agent_id(client: httpx.AsyncClient) -> None:
    response = await client.post("/v1/runs", json={"agent_id": "nope", "input": {"prompt": "x"}})
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    body = response.json()
    assert body["code"] == "unknown_agent_id"
    assert body["param"] == "agent_id"
    assert "Valid: agent-researcher, agent-simple, agent-flaky" in body["message"]


async def test_422_on_unexpected_field(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/v1/runs",
        json={"agent_id": "agent-simple", "input": {"prompt": "x"}, "unexpected": "field"},
    )
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


async def test_error_response_request_id_matches_header(client: httpx.AsyncClient) -> None:
    response = await client.get("/v1/runs/does-not-exist")
    assert "x-request-id" in response.headers
    assert response.headers["x-request-id"] == response.json()["request_id"]


async def test_success_response_carries_request_id_header(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")
    assert "x-request-id" in response.headers


async def test_incoming_request_id_is_propagated(client: httpx.AsyncClient) -> None:
    response = await client.get("/health", headers={"X-Request-Id": "my-custom-id"})
    assert response.headers["x-request-id"] == "my-custom-id"
