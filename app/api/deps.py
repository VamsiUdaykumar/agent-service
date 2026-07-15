from __future__ import annotations

from fastapi import Request

from app.services.run_service import RunService


def get_run_service(request: Request) -> RunService:
    run_service = request.app.state.run_service
    assert isinstance(run_service, RunService)
    return run_service
