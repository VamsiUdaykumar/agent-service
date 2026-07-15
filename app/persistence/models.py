"""Read-side projection shapes returned by the repository.

Distinct from `app.domain.events.Event`: these fold the event log into the
"current state" views (`runs`, `steps`) that the API reads. Mirrored later by
the API's Pydantic schemas (M4.T1.2) but kept independent here so the
persistence layer has no upward dependency on `app.api`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.domain.errors import RunError
from app.domain.status import RunStatus, StepStatus, StepType


@dataclass(frozen=True, slots=True)
class RunRecord:
    id: str
    status: RunStatus
    agent_id: str
    seed: int
    input: str
    metadata: dict[str, str] | None
    tokens_in: int
    tokens_out: int
    cost_usd: float
    trace_id: str
    error: RunError | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class StepRecord:
    run_id: str
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


@dataclass(frozen=True, slots=True)
class RunPage:
    data: list[RunRecord]
    has_more: bool
    next_cursor: str | None
