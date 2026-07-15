import random

from app.domain.status import StepType
from app.runner.pricing import MODEL_NAMES
from app.runner.simulate import sample_tokens_and_cost


def test_model_call_returns_tokens_cost_and_model_name() -> None:
    rng = random.Random(42)
    tokens_in, tokens_out, cost, model_name = sample_tokens_and_cost(StepType.MODEL_CALL, rng)
    assert tokens_in is not None and tokens_in > 0
    assert tokens_out is not None and tokens_out > 0
    assert cost is not None and cost > 0
    assert model_name in MODEL_NAMES


def test_non_model_call_steps_have_no_tokens_cost_or_model_name() -> None:
    rng = random.Random(42)
    for step_type in (StepType.TOOL_CALL, StepType.SUB_AGENT):
        assert sample_tokens_and_cost(step_type, rng) == (None, None, None, None)
