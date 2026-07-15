"""Per-request `request_id`: generated (or propagated from an incoming
header), stashed on `request.state`, and echoed back on every response —
the support-ticket-to-trace link (PRD §3.3).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-Id"


def install_request_id_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def _request_id_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
