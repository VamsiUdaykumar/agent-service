from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from opentelemetry.metrics import Meter
from opentelemetry.trace import Tracer

from app.api.errors import install_exception_handlers
from app.api.request_id import install_request_id_middleware
from app.api.routes.runs import router as runs_router
from app.config import Settings, get_settings
from app.persistence.sqlite_repository import SqliteRepository
from app.services.recovery import recover_orphaned_runs
from app.services.run_service import RunService
from app.telemetry.setup import configure_metrics, configure_tracing


def _make_lifespan(
    settings: Settings, tracer: Tracer | None, meter: Meter | None
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
        repository = await _connect_repository(settings)
        await recover_orphaned_runs(repository)

        # Real OTLP-exporting providers are only constructed when the
        # caller didn't already supply a tracer/meter (tests pass no-op
        # ones explicitly — spinning up a real provider per test pointed
        # at an unreachable collector makes shutdown block for seconds
        # retrying its export).
        tracer_provider = None if tracer is not None else configure_tracing(settings)
        meter_provider = None if meter is not None else configure_metrics(settings)
        effective_tracer = tracer or tracer_provider.get_tracer(settings.otel_service_name)  # type: ignore[union-attr]
        effective_meter = meter or meter_provider.get_meter(settings.otel_service_name)  # type: ignore[union-attr]

        app.state.repository = repository
        app.state.run_service = RunService(
            repository,
            settings.sim_speed,
            settings.idempotency_key_ttl_hours,
            tracer=effective_tracer,
            meter=effective_meter,
        )
        try:
            yield
        finally:
            await repository.close()
            if tracer_provider is not None:
                tracer_provider.shutdown()
            if meter_provider is not None:
                meter_provider.shutdown()

    return _lifespan


async def _connect_repository(settings: Settings) -> SqliteRepository:
    if settings.database_path != ":memory:":
        Path(settings.database_path).parent.mkdir(parents=True, exist_ok=True)
    return await SqliteRepository.connect(settings.database_path)


def create_app(
    settings: Settings | None = None, *, tracer: Tracer | None = None, meter: Meter | None = None
) -> FastAPI:
    settings = settings or get_settings()

    app = FastAPI(
        title="Agent Runs API",
        version=settings.otel_service_version,
        lifespan=_make_lifespan(settings, tracer, meter),
    )

    install_request_id_middleware(app)
    install_exception_handlers(app)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(runs_router, prefix="/v1")

    return app


app = create_app()
