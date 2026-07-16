"""`/v1/runs` — create, read, list. Reads answer purely from the repository
(via `RunService`) — never from the runner (PRD §3.1, §5).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from app.api.deps import get_run_service
from app.api.schemas import (
    ErrorEnvelope,
    RunCreateRequest,
    RunEnvelope,
    RunListResponse,
    StepListResponse,
    StepOut,
)
from app.domain.events import Event
from app.domain.status import RunStatus
from app.persistence.errors import RunNotFoundError
from app.services.run_service import RunService

router = APIRouter(prefix="/runs", tags=["runs"])

_METADATA_QUERY_PREFIX = "metadata."
_LAST_EVENT_ID_HEADER = "Last-Event-ID"

# FastAPI's default 422 response documents its own `HTTPValidationError`
# shape, which doesn't match what the app actually sends on the wire — the
# exception handlers in app/api/errors.py rewrite every error, including
# validation failures, into `ErrorEnvelope` (PRD §3.3). These per-route
# `responses=` overrides make `/docs` match reality instead of the default.
_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {"model": ErrorEnvelope, "description": "Run not found."}
}
_VALIDATION: dict[int | str, dict[str, Any]] = {
    status.HTTP_422_UNPROCESSABLE_CONTENT: {
        "model": ErrorEnvelope,
        "description": "Malformed request (body/query/path params).",
    }
}
_IDEMPOTENCY_CONFLICT: dict[int | str, dict[str, Any]] = {
    status.HTTP_409_CONFLICT: {
        "model": ErrorEnvelope,
        "description": "`Idempotency-Key` was already used with a different request body.",
    }
}
_TERMINAL_CONFLICT: dict[int | str, dict[str, Any]] = {
    status.HTTP_409_CONFLICT: {
        "model": ErrorEnvelope,
        "description": "The run is already in a terminal status (completed/failed/cancelled) "
        "— first terminal write wins.",
    }
}


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start a run",
    description=(
        "Durable-first: the run is persisted as `pending` *before* execution is spawned, so "
        "this `202` never lies — by the time you get a response, the run exists and is "
        "resumable even if the process crashes immediately after. Send an `Idempotency-Key` "
        "header to make retries of this call safe (PRD §3.3): the same key + same body replays "
        "the original response with no new run created; the same key + a different body is a "
        "`409 idempotency_error`."
    ),
    responses={**_VALIDATION, **_IDEMPOTENCY_CONFLICT},
)
async def create_run(
    body: RunCreateRequest,
    response: Response,
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
        description="Optional. Makes this call safe to retry (PRD §3.3).",
    ),
    service: RunService = Depends(get_run_service),
) -> RunEnvelope:
    record = await service.create_run(
        agent_id=body.agent_id,
        input=body.input,
        seed=body.seed,
        metadata=body.metadata,
        idempotency_key=idempotency_key,
    )
    response.headers["Location"] = f"/v1/runs/{record.id}"
    return RunEnvelope.from_record(record)


@router.get(
    "/{run_id}",
    summary="Get a run",
    description="The run envelope, folded from the event log — status, running totals "
    "(tokens, cost_usd), and trace_id. Safe to poll at any point in the run's lifecycle.",
    responses={**_NOT_FOUND, **_VALIDATION},
)
async def get_run(run_id: str, service: RunService = Depends(get_run_service)) -> RunEnvelope:
    record = await service.get_run(run_id)
    if record is None:
        raise RunNotFoundError(run_id)
    return RunEnvelope.from_record(record)


@router.get(
    "/{run_id}/steps",
    summary="Get a run's steps",
    description="Per-step state (type, status, attempt count, last_error, per-step tokens/cost). "
    "Always returned in full, never paginated — see `StepListResponse`.",
    responses={**_NOT_FOUND, **_VALIDATION},
)
async def get_steps(
    run_id: str, service: RunService = Depends(get_run_service)
) -> StepListResponse:
    record = await service.get_run(run_id)
    if record is None:
        raise RunNotFoundError(run_id)
    steps = await service.get_steps(run_id)
    return StepListResponse(data=[StepOut.from_record(step) for step in steps])


@router.get(
    "",
    summary="List runs",
    description=(
        "Cursor-paginated (`{data, has_more, next_cursor}`); the ULID run ID doubles as the "
        "cursor, sorted `created_at desc`. Filters: `status`, `agent_id`, `metadata.<key>` "
        "(repeat the prefix per key, e.g. `?metadata.customer_id=cust_123`), "
        "`created_after`/`created_before`."
    ),
    responses=_VALIDATION,
)
async def list_runs(
    request: Request,
    cursor: str | None = Query(
        default=None, description="Opaque cursor from a prior page's `next_cursor`."
    ),
    limit: int = Query(default=20, ge=1, le=100),
    status_filter: RunStatus | None = Query(default=None, alias="status"),
    agent_id: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    service: RunService = Depends(get_run_service),
) -> RunListResponse:
    metadata_filters = {
        key[len(_METADATA_QUERY_PREFIX) :]: value
        for key, value in request.query_params.items()
        if key.startswith(_METADATA_QUERY_PREFIX)
    }
    page = await service.list_runs(
        cursor=cursor,
        limit=limit,
        status=status_filter,
        agent_id=agent_id,
        metadata_filters=metadata_filters or None,
        created_after=created_after,
        created_before=created_before,
    )
    return RunListResponse(
        data=[RunEnvelope.from_record(record) for record in page.data],
        has_more=page.has_more,
        next_cursor=page.next_cursor,
    )


@router.post(
    "/{run_id}/cancel",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Cancel a run",
    description="Persists a `cancelling` transition and signals the in-process task. The runner "
    "stops at the next step boundary; a `finally` block guarantees a terminal `cancelled` event "
    "follows. Watch it happen live on the `/events` stream.",
    responses={**_NOT_FOUND, **_TERMINAL_CONFLICT, **_VALIDATION},
)
async def cancel_run(run_id: str, service: RunService = Depends(get_run_service)) -> RunEnvelope:
    record = await service.cancel_run(run_id)
    return RunEnvelope.from_record(record)


async def _format_sse(events: AsyncIterator[Event | None]) -> AsyncIterator[bytes]:
    async for event in events:
        if event is None:
            yield b": heartbeat\n\n"
            continue
        chunk = (
            f"id: {event.sequence}\nevent: {event.event_type}\ndata: {event.model_dump_json()}\n\n"
        )
        yield chunk.encode("utf-8")


@router.get(
    "/{run_id}/events",
    summary="Follow a run live (SSE)",
    description=(
        "Server-Sent Events tail of the persisted event log — never the runner directly, so "
        "this works identically whether the run is mid-execution or long finished. Historical "
        "events replay first, then new ones stream as they're appended; the connection closes "
        "after a terminal event. Reconnect with `Last-Event-ID: N` to resume from N+1, "
        "byte-identical to a fresh replay from that point. Idle connections get a `: heartbeat` "
        "comment every 15s to defeat proxy buffering timeouts."
    ),
    responses={
        **_NOT_FOUND,
        **_VALIDATION,
        status.HTTP_200_OK: {
            "description": "`text/event-stream` — one `id:`/`event:`/`data:` block per domain "
            "event (`run.started`, `step.started`, `step.completed`, …), or `: heartbeat` on idle.",
            "content": {"text/event-stream": {"schema": {"type": "string", "format": "binary"}}},
        },
    },
)
async def stream_events(
    run_id: str, request: Request, service: RunService = Depends(get_run_service)
) -> StreamingResponse:
    record = await service.get_run(run_id)
    if record is None:
        raise RunNotFoundError(run_id)

    last_event_id = request.headers.get(_LAST_EVENT_ID_HEADER)
    try:
        after_sequence = int(last_event_id) if last_event_id else 0
    except ValueError:
        after_sequence = 0  # malformed header — replay from the beginning

    return StreamingResponse(
        _format_sse(service.tail_events(run_id, after_sequence)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
