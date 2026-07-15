from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from opentelemetry.metrics import NoOpMeter
from opentelemetry.trace import NoOpTracer

from app.config import Settings
from app.main import create_app


def _make_settings(db_path: Path) -> Settings:
    return Settings(database_path=str(db_path), sim_speed=50_000.0)


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    settings = _make_settings(tmp_path / "test.db")
    # Explicit no-op tracer/meter: prevents `create_app` from standing up a
    # real OTLP-exporting provider per test — one pointed at an unreachable
    # collector would block for seconds on shutdown retrying its export.
    app = create_app(settings, tracer=NoOpTracer(), meter=NoOpMeter("test"))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
            yield http_client


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"
