"""`/v1/runs` — create, read, list. Reads answer purely from the repository
(via `RunService`) — never from the runner (PRD §3.1, §5).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from app.api.deps import get_run_service
from app.api.schemas import RunCreateRequest, RunEnvelope, RunListResponse, StepOut
from app.domain.events import Event
from app.domain.status import RunStatus
from app.persistence.errors import RunNotFoundError
from app.services.run_service import RunService

router = APIRouter(prefix="/runs", tags=["runs"])

_METADATA_QUERY_PREFIX = "metadata."
_LAST_EVENT_ID_HEADER = "Last-Event-ID"


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def create_run(
    body: RunCreateRequest,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
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


@router.get("/{run_id}")
async def get_run(run_id: str, service: RunService = Depends(get_run_service)) -> RunEnvelope:
    record = await service.get_run(run_id)
    if record is None:
        raise RunNotFoundError(run_id)
    return RunEnvelope.from_record(record)


@router.get("/{run_id}/steps")
async def get_steps(
    run_id: str, service: RunService = Depends(get_run_service)
) -> list[StepOut]:
    record = await service.get_run(run_id)
    if record is None:
        raise RunNotFoundError(run_id)
    steps = await service.get_steps(run_id)
    return [StepOut.from_record(step) for step in steps]


@router.get("")
async def list_runs(
    request: Request,
    cursor: str | None = None,
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


@router.post("/{run_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
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


@router.get("/{run_id}/events")
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
