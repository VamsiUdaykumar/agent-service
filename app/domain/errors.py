"""Structured error object attached to failed runs/steps.

Distinct from the API's `ErrorEnvelope` (app/api/schemas.py): this one lives
on the domain event/run, the API one wraps HTTP responses.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class RunErrorCode(StrEnum):
    """Closed set of domain error codes the runner and service layer may attach."""

    STEP_FAILED = "step_failed"
    INTERRUPTED_BY_RESTART = "interrupted_by_restart"
    CANCELLED_BY_USER = "cancelled_by_user"


class RunError(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: RunErrorCode
    message: str
    retryable: bool
