"""Agent profile definitions — a pure specification the runner (Milestone 3)
consumes. Lives here, not in app/runner, because it's data, not execution logic.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.status import StepType


class StepCountRange(BaseModel):
    model_config = ConfigDict(frozen=True)

    min_steps: Annotated[int, Field(ge=1)]
    max_steps: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def _check_range(self) -> StepCountRange:
        if self.max_steps < self.min_steps:
            raise ValueError("max_steps must be >= min_steps")
        return self


class AgentProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    agent_id: str
    step_count: StepCountRange
    step_type_weights: dict[StepType, float]
    fail_rate: Annotated[float, Field(ge=0.0, le=1.0)]
    non_retryable_rate: Annotated[float, Field(ge=0.0, le=1.0)]

    @model_validator(mode="after")
    def _check_weights(self) -> AgentProfile:
        if any(weight < 0 for weight in self.step_type_weights.values()):
            raise ValueError("step_type_weights must be non-negative")
        if sum(self.step_type_weights.values()) <= 0:
            raise ValueError("step_type_weights must sum to a positive value")
        return self


PROFILES: dict[str, AgentProfile] = {
    "agent-researcher": AgentProfile(
        agent_id="agent-researcher",
        step_count=StepCountRange(min_steps=5, max_steps=8),
        step_type_weights={
            StepType.MODEL_CALL: 0.6,
            StepType.TOOL_CALL: 0.3,
            StepType.SUB_AGENT: 0.1,
        },
        fail_rate=0.10,
        non_retryable_rate=0.0,
    ),
    "agent-simple": AgentProfile(
        agent_id="agent-simple",
        step_count=StepCountRange(min_steps=2, max_steps=3),
        step_type_weights={
            StepType.MODEL_CALL: 0.7,
            StepType.TOOL_CALL: 0.3,
            StepType.SUB_AGENT: 0.0,
        },
        fail_rate=0.02,
        non_retryable_rate=0.0,
    ),
    "agent-flaky": AgentProfile(
        agent_id="agent-flaky",
        step_count=StepCountRange(min_steps=4, max_steps=6),
        step_type_weights={
            StepType.MODEL_CALL: 0.5,
            StepType.TOOL_CALL: 0.4,
            StepType.SUB_AGENT: 0.1,
        },
        fail_rate=0.35,
        non_retryable_rate=0.05,
    ),
}
