"""Pure step-plan generation: given a profile and the recipe-seeded RNG,
produce the ordered list of steps for a run. No sleeping, no side effects —
`app.runner.execute` walks this plan and does the actual simulated work.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from app.domain.profiles import AgentProfile
from app.domain.status import StepType

# Sub-agent steps nest exactly one level deep (PRD §3.4), with 2-3 children —
# matching the "agent-simple" profile's 2-3 step count for consistency
# across the spec's small-count conventions.
_SUB_AGENT_CHILD_COUNT_RANGE = (2, 3)


@dataclass(frozen=True, slots=True)
class PlannedStep:
    step_id: str
    step_type: StepType
    children: tuple[PlannedStep, ...] = ()


StepPlan = tuple[PlannedStep, ...]


def _sample_step_type(rng: random.Random, weights: dict[StepType, float]) -> StepType:
    types = list(weights)
    return rng.choices(types, weights=[weights[t] for t in types], k=1)[0]


def generate_step_plan(profile: AgentProfile, rng: random.Random) -> StepPlan:
    step_count = rng.randint(profile.step_count.min_steps, profile.step_count.max_steps)

    # Children of a sub-agent step are drawn from the same mix minus
    # SUB_AGENT itself, renormalized — nesting stops at one level.
    child_weights: dict[StepType, float] = {
        step_type: weight
        for step_type, weight in profile.step_type_weights.items()
        if step_type is not StepType.SUB_AGENT and weight > 0
    }
    if not child_weights:
        child_weights = {StepType.MODEL_CALL: 1.0}

    steps: list[PlannedStep] = []
    for i in range(1, step_count + 1):
        step_type = _sample_step_type(rng, profile.step_type_weights)
        step_id = f"step-{i}"
        if step_type is StepType.SUB_AGENT:
            child_count = rng.randint(*_SUB_AGENT_CHILD_COUNT_RANGE)
            children = tuple(
                PlannedStep(
                    step_id=f"{step_id}.{j}", step_type=_sample_step_type(rng, child_weights)
                )
                for j in range(1, child_count + 1)
            )
            steps.append(PlannedStep(step_id=step_id, step_type=step_type, children=children))
        else:
            steps.append(PlannedStep(step_id=step_id, step_type=step_type))

    return tuple(steps)
