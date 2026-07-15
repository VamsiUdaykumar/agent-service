"""M5.T7: SSE resume + cancellation over the real HTTP surface.

These tests need finer-grained timing control than the shared `client`
fixture's very fast SIM_SPEED gives (a run could finish before we get a
chance to disconnect/cancel mid-flight), so each test builds its own app
with a slower simulated clock via `tmp_path` directly.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from opentelemetry.metrics import NoOpMeter
from opentelemetry.trace import NoOpTracer
from starlette import status

from app.config import Settings
from app.main import create_app


async def _make_client(tmp_path: Path, name: str, sim_speed: float) -> httpx.AsyncClient:
    settings = Settings(database_path=str(tmp_path / name), sim_speed=sim_speed)
    app = create_app(settings, tracer=NoOpTracer(), meter=NoOpMeter("test"))
    ctx = app.router.lifespan_context(app)
    await ctx.__aenter__()
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")
    client._lifespan_ctx = ctx  # type: ignore[attr-defined]
    return client


async def _close_client(client: httpx.AsyncClient) -> None:
    await client.aclose()
    await client._lifespan_ctx.__aexit__(None, None, None)  # type: ignore[attr-defined]


async def _iter_sse(response: httpx.Response) -> AsyncIterator[tuple[int, str, dict]]:
    seq: int | None = None
    event_type: str | None = None
    data: dict | None = None
    async for line in response.aiter_lines():
        if line.startswith("id:"):
            seq = int(line[len("id:") :].strip())
        elif line.startswith("event:"):
            event_type = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data = json.loads(line[len("data:") :].strip())
        elif line == "":
            if seq is not None and event_type is not None and data is not None:
                yield seq, event_type, data
            seq = event_type = data = None
        # lines starting with ":" are comments (heartbeats) — ignored


async def _poll_until_terminal(client: httpx.AsyncClient, run_id: str) -> dict:
    for _ in range(1000):
        response = await client.get(f"/v1/runs/{run_id}")
        data = response.json()
        if data["status"] in ("completed", "failed", "cancelled"):
            return data
        await asyncio.sleep(0.01)
    pytest.fail(f"run {run_id} did not reach a terminal state in time")


async def test_sse_stream_starts_with_run_created_and_content_type(tmp_path: Path) -> None:
    client = await _make_client(tmp_path, "sse1.db", sim_speed=50_000.0)
    try:
        create_resp = await client.post(
            "/v1/runs", json={"agent_id": "agent-simple", "input": {"prompt": "x"}, "seed": 1}
        )
        run_id = create_resp.json()["id"]
        await _poll_until_terminal(client, run_id)

        async with client.stream("GET", f"/v1/runs/{run_id}/events") as response:
            assert response.status_code == status.HTTP_200_OK
            assert "text/event-stream" in response.headers["content-type"]
            entries = [entry async for entry in _iter_sse(response)]

        assert entries[0] == (1, "run_created", entries[0][2])
        assert entries[-1][1] in ("run_completed", "run_failed")
        assert [e[0] for e in entries] == list(range(1, len(entries) + 1))
    finally:
        await _close_client(client)


async def test_sse_events_endpoint_404s_for_unknown_run(tmp_path: Path) -> None:
    client = await _make_client(tmp_path, "sse2.db", sim_speed=50_000.0)
    try:
        response = await client.get("/v1/runs/does-not-exist/events")
        assert response.status_code == status.HTTP_404_NOT_FOUND
    finally:
        await _close_client(client)


async def test_sse_resume_after_disconnect_matches_full_replay(tmp_path: Path) -> None:
    client = await _make_client(tmp_path, "sse3.db", sim_speed=20.0)
    try:
        create_resp = await client.post(
            "/v1/runs",
            json={"agent_id": "agent-researcher", "input": {"prompt": "x"}, "seed": 1},
        )
        run_id = create_resp.json()["id"]

        # Connect and read a couple of events, then disconnect early.
        partial: list[tuple[int, str, dict]] = []
        async with client.stream("GET", f"/v1/runs/{run_id}/events") as response:
            async for entry in _iter_sse(response):
                partial.append(entry)
                if len(partial) >= 2:
                    break
        assert len(partial) >= 2
        last_seen_id = partial[-1][0]

        await _poll_until_terminal(client, run_id)

        # Reconnect with Last-Event-ID to get exactly the missed tail.
        resumed: list[tuple[int, str, dict]] = []
        async with client.stream(
            "GET", f"/v1/runs/{run_id}/events", headers={"Last-Event-ID": str(last_seen_id)}
        ) as response:
            async for entry in _iter_sse(response):
                resumed.append(entry)

        # A fresh full replay from the beginning.
        full: list[tuple[int, str, dict]] = []
        async with client.stream("GET", f"/v1/runs/{run_id}/events") as response:
            async for entry in _iter_sse(response):
                full.append(entry)

        union = partial + resumed
        assert union == full
        assert len({e[0] for e in union}) == len(union)  # no duplicate sequences
    finally:
        await _close_client(client)


async def test_cancel_while_running_reaches_cancelled_and_second_cancel_409s(
    tmp_path: Path,
) -> None:
    client = await _make_client(tmp_path, "sse4.db", sim_speed=20.0)
    try:
        create_resp = await client.post(
            "/v1/runs",
            json={"agent_id": "agent-researcher", "input": {"prompt": "x"}, "seed": 1},
        )
        run_id = create_resp.json()["id"]

        await asyncio.sleep(0.03)  # let the run get underway

        cancel_resp = await client.post(f"/v1/runs/{run_id}/cancel")
        assert cancel_resp.status_code == status.HTTP_202_ACCEPTED
        assert cancel_resp.json()["status"] in ("cancelling", "cancelled")

        final = await _poll_until_terminal(client, run_id)
        assert final["status"] == "cancelled"

        second = await client.post(f"/v1/runs/{run_id}/cancel")
        assert second.status_code == status.HTTP_409_CONFLICT
        assert second.json()["type"] == "conflict"

        # cancelling -> cancelled is visible on the replayed stream.
        async with client.stream("GET", f"/v1/runs/{run_id}/events") as response:
            entries = [entry async for entry in _iter_sse(response)]
        event_types = [e[1] for e in entries]
        assert "run_cancelling" in event_types
        assert "run_cancelled" in event_types
        assert event_types.index("run_cancelling") < event_types.index("run_cancelled")
    finally:
        await _close_client(client)


async def test_cancel_unknown_run_404s(tmp_path: Path) -> None:
    client = await _make_client(tmp_path, "sse5.db", sim_speed=50_000.0)
    try:
        response = await client.post("/v1/runs/does-not-exist/cancel")
        assert response.status_code == status.HTTP_404_NOT_FOUND
    finally:
        await _close_client(client)
