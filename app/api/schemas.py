"""Request/response schemas — the envelope shape shared between create and
read, and the one `ErrorEnvelope` every error response uses (PRD §3.3).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, field_validator

from app.domain.errors import RunError
from app.domain.status import RunStatus, StepStatus, StepType
from app.persistence.models import RunRecord, StepRecord

MAX_INPUT_BYTES = 32 * 1024


class RunCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str
    input: dict[str, Any]
    seed: int | None = None
    metadata: dict[str, str] | None = None

    @field_validator("input")
    @classmethod
    def _check_input_size(cls, value: dict[str, Any]) -> dict[str, Any]:
        size = len(json.dumps(value, separators=(",", ":")).encode("utf-8"))
        if size > MAX_INPUT_BYTES:
            raise ValueError(f"input must be at most {MAX_INPUT_BYTES} bytes when JSON-encoded")
        return value


class RunEnvelope(BaseModel):
    id: str
    status: RunStatus
    agent_id: str
    seed: int
    input: dict[str, Any]
    metadata: dict[str, str] | None
    tokens_in: int
    tokens_out: int
    cost_usd: float
    trace_id: str
    error: RunError | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_record(cls, record: RunRecord) -> Self:
        return cls(
            id=record.id,
            status=record.status,
            agent_id=record.agent_id,
            seed=record.seed,
            input=record.input,
            metadata=record.metadata,
            tokens_in=record.tokens_in,
            tokens_out=record.tokens_out,
            cost_usd=record.cost_usd,
            trace_id=record.trace_id,
            error=record.error,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class StepOut(BaseModel):
    step_id: str
    parent_step_id: str | None
    step_type: StepType
    status: StepStatus
    attempt: int
    tokens_in: int
    tokens_out: int
    cost_usd: float
    last_error: RunError | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_record(cls, record: StepRecord) -> Self:
        return cls(
            step_id=record.step_id,
            parent_step_id=record.parent_step_id,
            step_type=record.step_type,
            status=record.status,
            attempt=record.attempt,
            tokens_in=record.tokens_in,
            tokens_out=record.tokens_out,
            cost_usd=record.cost_usd,
            last_error=record.last_error,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )


class ErrorEnvelope(BaseModel):
    type: Literal["invalid_request", "not_found", "conflict", "idempotency_error", "internal_error"]
    code: str
    message: str
    param: str | None = None
    request_id: str


class RunListResponse(BaseModel):
    data: list[RunEnvelope]
    has_more: bool
    next_cursor: str | None
