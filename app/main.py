from fastapi import FastAPI

from app.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Agent Runs API",
        version=settings.otel_service_version,
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    # Routers are registered here as they land (Milestone 4+):
    #   app.include_router(runs_router, prefix="/v1")

    return app


app = create_app()
