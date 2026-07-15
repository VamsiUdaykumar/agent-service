"""Append-only event types — the schema the SQLite event log (M2) and the
SSE stream (M5) both serve directly.

`RunCreated` is appended by the service layer at persist time (M4.T3.1), not
yielded by the runner. Every other event is yielded by the runner's
execution generator (M3.T6) as it walks a run's step plan.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.domain.errors import RunError
from app.domain.status import StepType


class BaseEvent(BaseModel):
    """Common envelope for every event. Sequence numbers are 1-indexed per run."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    sequence: Annotated[int, Field(ge=1)]
    occurred_at: datetime


class RunCreated(BaseEvent):
    event_type: Literal["run_created"] = "run_created"
    agent_id: str
    seed: int
    input: dict[str, Any]
    metadata: dict[str, str] | None = None
    trace_id: str


class RunStarted(BaseEvent):
    event_type: Literal["run_started"] = "run_started"


class StepStarted(BaseEvent):
    event_type: Literal["step_started"] = "step_started"
    step_id: str
    step_type: StepType
    attempt: Annotated[int, Field(ge=1)]
    parent_step_id: str | None = None


class StepCompleted(BaseEvent):
    event_type: Literal["step_completed"] = "step_completed"
    step_id: str
    attempt: Annotated[int, Field(ge=1)]
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None
    duration_ms: int


class StepFailed(BaseEvent):
    event_type: Literal["step_failed"] = "step_failed"
    step_id: str
    attempt: Annotated[int, Field(ge=1)]
    error: RunError
    duration_ms: int
    # Failed attempts still consume tokens (PRD §3.2 — failure costs money,
    # and that must be visible in accounting/analytics).
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None


class StepRetried(BaseEvent):
    event_type: Literal["step_retried"] = "step_retried"
    step_id: str
    next_attempt: Annotated[int, Field(ge=2)]
    delay_ms: int


class RunCompleted(BaseEvent):
    event_type: Literal["run_completed"] = "run_completed"
    tokens_in: int
    tokens_out: int
    cost_usd: float
    duration_ms: int


class RunFailed(BaseEvent):
    event_type: Literal["run_failed"] = "run_failed"
    error: RunError
    tokens_in: int
    tokens_out: int
    cost_usd: float
    duration_ms: int


class RunCancelled(BaseEvent):
    event_type: Literal["run_cancelled"] = "run_cancelled"
    tokens_in: int
    tokens_out: int
    cost_usd: float
    duration_ms: int


Event = Annotated[
    RunCreated
    | RunStarted
    | StepStarted
    | StepCompleted
    | StepFailed
    | StepRetried
    | RunCompleted
    | RunFailed
    | RunCancelled,
    Field(discriminator="event_type"),
]

# Registry so the persistence layer can (de)serialize generically by the
# `event_type` string stored alongside the JSON payload.
EVENT_TYPES: dict[str, type[BaseEvent]] = {
    "run_created": RunCreated,
    "run_started": RunStarted,
    "step_started": StepStarted,
    "step_completed": StepCompleted,
    "step_failed": StepFailed,
    "step_retried": StepRetried,
    "run_completed": RunCompleted,
    "run_failed": RunFailed,
    "run_cancelled": RunCancelled,
}

_event_adapter: TypeAdapter[Event] = TypeAdapter(Event)


def parse_event(data: dict[str, object]) -> Event:
    """Parse a raw dict (e.g. decoded from a stored JSON payload) into its concrete event type."""
    return _event_adapter.validate_python(data)
