"""Request/response schemas — the envelope shape shared between create and
read, and the one `ErrorEnvelope` every error response uses (PRD §3.3).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from app.domain.errors import RunError
from app.domain.status import RunStatus, StepStatus, StepType
from app.persistence.models import RunRecord, StepRecord

MAX_INPUT_BYTES = 32 * 1024
# Storage keeps full float precision; the wire format rounds to the same
# 6dp the dashboard's currencyUSD panels use, so API responses don't leak
# float noise like 0.08907000000000001.
COST_USD_DECIMALS = 6

_EXAMPLE_INPUT = {"prompt": "summarize the attached document"}
_RUN_CREATE_EXAMPLE: dict[str, Any] = {
    "examples": [
        {
            "agent_id": "agent-researcher",
            "input": _EXAMPLE_INPUT,
            "seed": 42,
            "metadata": {"customer_id": "cust_123"},
        }
    ]
}


class RunCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra=_RUN_CREATE_EXAMPLE)

    agent_id: str = Field(
        description="Selects a behavior profile: agent-researcher, agent-simple, or agent-flaky.",
        examples=["agent-researcher"],
    )
    input: dict[str, Any] = Field(
        description="Arbitrary JSON payload, part of the deterministic recipe. ≤32KB JSON-encoded.",
        examples=[_EXAMPLE_INPUT],
    )
    seed: int | None = Field(
        default=None,
        description="Omit to have the server generate one (returned on the envelope) — "
        "every run, chosen or generated, is replayable from its recipe alone.",
    )
    metadata: dict[str, str] | None = Field(
        default=None,
        description="Opaque string→string tags. Filterable via `metadata.<key>` on the list "
        "endpoint and attached as span attributes.",
    )

    @field_validator("input")
    @classmethod
    def _check_input_size(cls, value: dict[str, Any]) -> dict[str, Any]:
        size = len(json.dumps(value, separators=(",", ":")).encode("utf-8"))
        if size > MAX_INPUT_BYTES:
            raise ValueError(f"input must be at most {MAX_INPUT_BYTES} bytes when JSON-encoded")
        return value


class RunEnvelope(BaseModel):
    """The run's current state, folded from the event log — never from the
    runner or OTel (the store is the source of truth). Identical shape on
    creation, on poll, and after cancel; only `status` and the totals change
    over the run's lifetime.
    """

    id: str
    status: RunStatus
    agent_id: str
    seed: int = Field(description="Echoed back — server-generated if the request omitted it.")
    input: dict[str, Any]
    metadata: dict[str, str] | None
    tokens_in: int
    tokens_out: int
    cost_usd: float = Field(description="Rounded to 6dp; storage keeps full precision.")
    trace_id: str = Field(
        description="Set at creation, before the runner starts — open this in Grafana/Tempo "
        "to see the run's span waterfall."
    )
    error: RunError | None
    created_at: datetime
    updated_at: datetime

    @field_serializer("cost_usd")
    def _round_cost(self, value: float) -> float:
        return round(value, COST_USD_DECIMALS)

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
    parent_step_id: str | None = Field(
        description="Set only for a sub-agent's nested children (one level deep)."
    )
    step_type: StepType
    status: StepStatus
    attempt: int = Field(description="1-indexed. >1 means this step retried at least once.")
    tokens_in: int
    tokens_out: int
    cost_usd: float = Field(description="Rounded to 6dp; storage keeps full precision.")
    last_error: RunError | None
    created_at: datetime
    updated_at: datetime

    @field_serializer("cost_usd")
    def _round_cost(self, value: float) -> float:
        return round(value, COST_USD_DECIMALS)

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
    """The one error shape every non-2xx response uses, including validation
    errors (FastAPI's default 422 handler is overridden to match).
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "type": "invalid_request",
                    "code": "unknown_agent_id",
                    "message": "unknown agent_id: 'agent-mystery'. Valid: agent-researcher, "
                    "agent-simple, agent-flaky",
                    "param": "agent_id",
                    "request_id": "01JZ9X8K3Q7YB2N4M6P8R0T2W4",
                }
            ]
        }
    )

    type: Literal[
        "invalid_request", "not_found", "conflict", "idempotency_error", "internal_error"
    ] = Field(description="Closed set — stable enough for a client to branch on.")
    code: str = Field(description="Growable specific case, e.g. `run_not_found`, `run_terminal`.")
    message: str
    param: str | None = Field(
        default=None, description="The request field/header this error is attributed to, if any."
    )
    request_id: str = Field(description="Also attached to the trace — support-ticket-to-trace link")


class RunListResponse(BaseModel):
    data: list[RunEnvelope]
    has_more: bool
    next_cursor: str | None


class StepListResponse(BaseModel):
    """Steps are always returned in full — a run's step count is small and
    bounded by its profile (PRD §4: at most 8 top-level steps, one level of
    sub-agent nesting), so there's no cursor to advance. `has_more` is
    always `false` and `next_cursor` always `null`; the shape is still the
    shared `{data, has_more, next_cursor}` list envelope (PRD §3.3) rather
    than a bare array, so a client never needs a special case to tell
    whether a given list-shaped endpoint is paginated.
    """

    data: list[StepOut]
    has_more: bool = False
    next_cursor: str | None = None
