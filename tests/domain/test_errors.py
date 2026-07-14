import pytest
from pydantic import ValidationError

from app.domain.errors import RunError, RunErrorCode


def test_closed_error_code_set() -> None:
    assert {code.value for code in RunErrorCode} == {
        "step_failed",
        "interrupted_by_restart",
        "cancelled_by_user",
    }


def test_run_error_round_trips_and_is_frozen() -> None:
    error = RunError(code=RunErrorCode.STEP_FAILED, message="tool timed out", retryable=True)
    restored = RunError.model_validate(error.model_dump(mode="json"))
    assert restored == error

    with pytest.raises(ValidationError):
        error.code = RunErrorCode.CANCELLED_BY_USER  # type: ignore[misc]


def test_unknown_code_rejected() -> None:
    with pytest.raises(ValidationError):
        RunError.model_validate({"code": "not_a_code", "message": "x", "retryable": False})
