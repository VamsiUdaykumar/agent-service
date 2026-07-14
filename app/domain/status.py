"""Status enums and the run-status transition graph. Pure, no I/O."""

from __future__ import annotations

from enum import StrEnum


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class StepType(StrEnum):
    MODEL_CALL = "model_call"
    TOOL_CALL = "tool_call"
    SUB_AGENT = "sub_agent"


# Adjacency map, not an if/elif chain, so the legal graph is auditable at a glance.
# cancelling -> completed is legal: the step in flight when cancellation was
# requested may still finish successfully before the runner checks the next
# step boundary (amendment 1) — the store's "first terminal write wins" rule
# is what actually adjudicates the race, this graph just says both are legal.
_RUN_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.PENDING: frozenset({RunStatus.RUNNING, RunStatus.CANCELLING, RunStatus.FAILED}),
    RunStatus.RUNNING: frozenset(
        {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLING}
    ),
    RunStatus.CANCELLING: frozenset(
        {RunStatus.CANCELLED, RunStatus.COMPLETED, RunStatus.FAILED}
    ),
    RunStatus.COMPLETED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
}

TERMINAL_RUN_STATUSES: frozenset[RunStatus] = frozenset(
    {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}
)


def can_transition(from_status: RunStatus, to_status: RunStatus) -> bool:
    """True iff `to_status` is a legal next status from `from_status`."""
    return to_status in _RUN_TRANSITIONS[from_status]


def is_terminal(status: RunStatus) -> bool:
    """True for completed/failed/cancelled — statuses that accept no further transitions."""
    return status in TERMINAL_RUN_STATUSES
