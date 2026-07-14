import pytest

from app.domain.profiles import PROFILES, AgentProfile, StepCountRange
from app.domain.status import StepType


def test_all_three_named_profiles_present() -> None:
    assert set(PROFILES) == {"researcher", "simple", "flaky"}


@pytest.mark.parametrize("agent_id,profile", sorted(PROFILES.items()))
def test_profile_resolves_to_a_valid_config(agent_id: str, profile: AgentProfile) -> None:
    assert profile.agent_id == agent_id
    assert profile.step_count.min_steps <= profile.step_count.max_steps
    assert 0.0 <= profile.fail_rate <= 1.0
    assert 0.0 <= profile.non_retryable_rate <= 1.0
    assert sum(profile.step_type_weights.values()) > 0
    assert all(weight >= 0 for weight in profile.step_type_weights.values())
    assert set(profile.step_type_weights) <= set(StepType)


def test_researcher_and_simple_have_low_fail_rates_flaky_is_high() -> None:
    assert PROFILES["researcher"].fail_rate == pytest.approx(0.10)
    assert PROFILES["simple"].fail_rate == pytest.approx(0.02)
    assert PROFILES["flaky"].fail_rate == pytest.approx(0.35)
    assert PROFILES["flaky"].non_retryable_rate == pytest.approx(0.05)


def test_step_count_range_rejects_max_below_min() -> None:
    with pytest.raises(ValueError):
        StepCountRange(min_steps=5, max_steps=2)


def test_agent_profile_rejects_all_zero_weights() -> None:
    with pytest.raises(ValueError):
        AgentProfile(
            agent_id="broken",
            step_count=StepCountRange(min_steps=1, max_steps=2),
            step_type_weights={StepType.MODEL_CALL: 0.0},
            fail_rate=0.1,
            non_retryable_rate=0.0,
        )
