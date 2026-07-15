"""Per-step token, cost, and latency sampling — all draws come from the
recipe-seeded RNG (app.runner.rng), never the wall clock or os.urandom.
"""

from __future__ import annotations

import random

from app.domain.status import StepType
from app.runner.pricing import MODEL_NAMES, cost_usd

# Only model_call steps draw tokens/cost; tool_call and sub_agent steps have
# none of their own (a sub-agent's cost is the sum of its children's).
_TOKEN_RANGES: dict[StepType, tuple[tuple[int, int], tuple[int, int]]] = {
    StepType.MODEL_CALL: ((100, 2000), (50, 1000)),
}

_LATENCY_MS_RANGES: dict[StepType, tuple[int, int]] = {
    StepType.MODEL_CALL: (300, 2500),
    StepType.TOOL_CALL: (50, 800),
    StepType.SUB_AGENT: (100, 500),
}

_MODEL_WEIGHTS = (0.7, 0.3)  # mostly the cheap model, sometimes the pro model

# Fixed exponential backoff schedule: 0.5s before the 1st retry, 1s before
# the 2nd. Not RNG-sampled — a deterministic function of the failed attempt
# number, so it doesn't consume RNG state at all.
_BACKOFF_SCHEDULE_MS: tuple[int, ...] = (500, 1000)


def sample_model_name(rng: random.Random) -> str:
    return rng.choices(MODEL_NAMES, weights=_MODEL_WEIGHTS, k=1)[0]


def sample_tokens_and_cost(
    step_type: StepType, rng: random.Random
) -> tuple[int | None, int | None, float | None]:
    ranges = _TOKEN_RANGES.get(step_type)
    if ranges is None:
        return None, None, None
    (in_lo, in_hi), (out_lo, out_hi) = ranges
    tokens_in = rng.randint(in_lo, in_hi)
    tokens_out = rng.randint(out_lo, out_hi)
    model_name = sample_model_name(rng)
    return tokens_in, tokens_out, cost_usd(model_name, tokens_in, tokens_out)


def sample_latency_ms(step_type: StepType, rng: random.Random) -> int:
    lo, hi = _LATENCY_MS_RANGES[step_type]
    return rng.randint(lo, hi)


def backoff_delay_ms(attempt: int) -> int:
    """Delay before retrying after `attempt` (1-indexed) has failed."""
    index = min(attempt - 1, len(_BACKOFF_SCHEDULE_MS) - 1)
    return _BACKOFF_SCHEDULE_MS[index]
