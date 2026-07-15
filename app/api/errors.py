"""Exception handlers: every error response — including validation errors —
uses the one `ErrorEnvelope` shape (PRD §2, §3.3). 5xx bodies never leak
internals; the real exception is logged server-side instead.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.schemas import ErrorEnvelope
from app.persistence.errors import (
    IdempotencyConflictError,
    IllegalTransitionError,
    RunNotFoundError,
    TerminalRunConflictError,
)
from app.services.errors import UnknownAgentError

logger = logging.getLogger(__name__)

# Domain-error -> HTTP-status mapping, as one lookup table (M4.T2.4).
# The 4th element is the `param` the error is attributed to, where applicable.
_DOMAIN_ERROR_MAPPING: dict[type[Exception], tuple[int, str, str, str | None]] = {
    RunNotFoundError: (status.HTTP_404_NOT_FOUND, "not_found", "run_not_found", None),
    TerminalRunConflictError: (status.HTTP_409_CONFLICT, "conflict", "run_terminal", None),
    IllegalTransitionError: (
        status.HTTP_409_CONFLICT, "conflict", "illegal_transition", None
    ),
    UnknownAgentError: (
        status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_request", "unknown_agent_id", "agent_id"
    ),
    IdempotencyConflictError: (
        status.HTTP_409_CONFLICT, "idempotency_error", "idempotency_key_conflict",
        "Idempotency-Key",
    ),
}


def _request_id(request: Request) -> str:
    request_id = getattr(request.state, "request_id", None)
    return request_id if isinstance(request_id, str) else "unknown"


def _envelope_response(
    request: Request,
    status_code: int,
    error_type: str,
    code: str,
    message: str,
    param: str | None = None,
) -> JSONResponse:
    envelope = ErrorEnvelope(
        type=error_type,  # type: ignore[arg-type]
        code=code,
        message=message,
        param=param,
        request_id=_request_id(request),
    )
    return JSONResponse(status_code=status_code, content=envelope.model_dump(mode="json"))


async def _validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    errors = exc.errors()
    first = errors[0] if errors else {}
    param = ".".join(str(part) for part in first.get("loc", ())) or None
    message = str(first.get("msg", "invalid request"))
    return _envelope_response(
        request, status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_request", "validation_error",
        message, param,
    )


async def _domain_error_handler(request: Request, exc: Exception) -> JSONResponse:
    status_code, error_type, code, param = _DOMAIN_ERROR_MAPPING[type(exc)]
    return _envelope_response(request, status_code, error_type, code, str(exc), param)


async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled exception while handling %s %s", request.method, request.url.path)
    return _envelope_response(
        request, status.HTTP_500_INTERNAL_SERVER_ERROR, "internal_error", "internal_error",
        "an internal error occurred",
    )


def install_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    for exc_type in _DOMAIN_ERROR_MAPPING:
        app.add_exception_handler(exc_type, _domain_error_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)
