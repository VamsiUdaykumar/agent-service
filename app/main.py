from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app.api.errors import install_exception_handlers
from app.api.request_id import install_request_id_middleware
from app.api.routes.runs import router as runs_router
from app.config import Settings, get_settings
from app.persistence.sqlite_repository import SqliteRepository
from app.services.recovery import recover_orphaned_runs
from app.services.run_service import RunService


def _make_lifespan(settings: Settings) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
        repository = await _connect_repository(settings)
        await recover_orphaned_runs(repository)
        app.state.repository = repository
        app.state.run_service = RunService(
            repository, settings.sim_speed, settings.idempotency_key_ttl_hours
        )
        try:
            yield
        finally:
            await repository.close()

    return _lifespan


async def _connect_repository(settings: Settings) -> SqliteRepository:
    if settings.database_path != ":memory:":
        Path(settings.database_path).parent.mkdir(parents=True, exist_ok=True)
    return await SqliteRepository.connect(settings.database_path)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    app = FastAPI(
        title="Agent Runs API",
        version=settings.otel_service_version,
        lifespan=_make_lifespan(settings),
    )

    install_request_id_middleware(app)
    install_exception_handlers(app)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(runs_router, prefix="/v1")

    return app


app = create_app()
