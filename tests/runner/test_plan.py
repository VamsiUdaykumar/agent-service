from app.domain.profiles import PROFILES
from app.domain.status import StepType
from app.runner.plan import generate_step_plan
from app.runner.rng import make_rng


def _flatten(plan):
    for step in plan:
        yield step
        yield from step.children


def test_step_count_within_profile_range() -> None:
    profile = PROFILES["agent-researcher"]
    for seed in range(50):
        rng = make_rng("agent-researcher", seed, {"prompt": "x"})
        plan = generate_step_plan(profile, rng)
        assert profile.step_count.min_steps <= len(plan) <= profile.step_count.max_steps


def test_step_types_drawn_only_from_profile_weights() -> None:
    profile = PROFILES["agent-researcher"]
    allowed = {t for t, w in profile.step_type_weights.items() if w > 0}
    for seed in range(50):
        rng = make_rng("agent-researcher", seed, {"prompt": "x"})
        plan = generate_step_plan(profile, rng)
        for step in plan:
            assert step.step_type in allowed
        for step in plan:
            for child in step.children:
                assert child.step_type in allowed
                assert child.step_type is not StepType.SUB_AGENT


def test_sub_agent_nests_exactly_one_level_deep() -> None:
    profile = PROFILES["agent-researcher"]
    found_sub_agent = False
    for seed in range(200):
        rng = make_rng("agent-researcher", seed, {"prompt": "x"})
        plan = generate_step_plan(profile, rng)
        for step in plan:
            if step.step_type is StepType.SUB_AGENT:
                found_sub_agent = True
                assert len(step.children) > 0
                for child in step.children:
                    assert child.step_type is not StepType.SUB_AGENT
                    assert child.children == ()
    assert found_sub_agent, "expected at least one sub_agent step across 200 seeds"


def test_step_ids_are_unique_within_a_plan() -> None:
    profile = PROFILES["agent-researcher"]
    rng = make_rng("agent-researcher", 1, {"prompt": "x"})
    plan = generate_step_plan(profile, rng)
    ids = [step.step_id for step in _flatten(plan)]
    assert len(ids) == len(set(ids))


def test_generate_step_plan_is_a_pure_function_of_the_rng_state() -> None:
    profile = PROFILES["agent-flaky"]
    plan_a = generate_step_plan(profile, make_rng("agent-flaky", 99, {"prompt": "task"}))
    plan_b = generate_step_plan(profile, make_rng("agent-flaky", 99, {"prompt": "task"}))
    assert plan_a == plan_b
