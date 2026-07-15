import asyncio
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.domain.events import RunStarted
from app.main import create_app
from app.persistence.sqlite_repository import SqliteRepository


async def _poll_until_terminal(client: httpx.AsyncClient, run_id: str) -> dict:
    for _ in range(500):
        response = await client.get(f"/v1/runs/{run_id}")
        data = response.json()
        if data["status"] in ("completed", "failed", "cancelled"):
            return data
        await asyncio.sleep(0.01)
    pytest.fail(f"run {run_id} did not reach a terminal state in time")


async def _seed_orphaned_run(db_path: Path, run_id: str, status: str) -> None:
    """Seed a run directly through the repository, bypassing the app, to
    simulate one left behind by a process that died mid-run.
    """
    repo = await SqliteRepository.connect(str(db_path))
    try:
        now = datetime.now(UTC)
        await repo.create_run(
            run_id=run_id,
            agent_id="agent-simple",
            seed=1,
            input={"prompt": "orphaned"},
            metadata=None,
            trace_id="a" * 32,
            created_at=now,
        )
        if status != "pending":
            await repo.append_event(RunStarted(run_id=run_id, sequence=1, occurred_at=now))
    finally:
        await repo.close()


async def test_startup_recovery_flips_orphaned_pending_run_to_failed(db_path: Path) -> None:
    await _seed_orphaned_run(db_path, "orphan-pending", status="pending")

    settings = Settings(database_path=str(db_path), sim_speed=50_000.0)
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/v1/runs/orphan-pending")
            body = response.json()
            assert body["status"] == "failed"
            assert body["error"]["code"] == "interrupted_by_restart"
            assert body["error"]["retryable"] is False


async def test_startup_recovery_flips_orphaned_running_run_to_failed(db_path: Path) -> None:
    await _seed_orphaned_run(db_path, "orphan-running", status="running")

    settings = Settings(database_path=str(db_path), sim_speed=50_000.0)
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/v1/runs/orphan-running")
            body = response.json()
            assert body["status"] == "failed"
            assert body["error"]["code"] == "interrupted_by_restart"


async def test_startup_recovery_does_not_touch_healthy_terminal_runs(db_path: Path) -> None:
    settings = Settings(database_path=str(db_path), sim_speed=50_000.0)
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/runs", json={"agent_id": "agent-simple", "input": {"prompt": "x"}, "seed": 1}
            )
            run_id = response.json()["id"]
            await _poll_until_terminal(client, run_id)

    # Reopen the app against the same DB — a normal restart, nothing orphaned.
    app2 = create_app(settings)
    async with app2.router.lifespan_context(app2):
        transport2 = httpx.ASGITransport(app=app2)
        async with httpx.AsyncClient(transport=transport2, base_url="http://test") as client2:
            response = await client2.get(f"/v1/runs/{run_id}")
            assert response.json()["status"] in ("completed", "failed")
            assert response.json()["error"] is None or (
                response.json()["error"]["code"] != "interrupted_by_restart"
            )
