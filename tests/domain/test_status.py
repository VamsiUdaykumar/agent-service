import itertools

import pytest

from app.domain.status import RunStatus, can_transition, is_terminal

ALL_STATUSES = list(RunStatus)

LEGAL_TRANSITIONS = {
    (RunStatus.PENDING, RunStatus.RUNNING),
    (RunStatus.PENDING, RunStatus.CANCELLING),
    (RunStatus.PENDING, RunStatus.FAILED),
    (RunStatus.RUNNING, RunStatus.COMPLETED),
    (RunStatus.RUNNING, RunStatus.FAILED),
    (RunStatus.RUNNING, RunStatus.CANCELLING),
    (RunStatus.CANCELLING, RunStatus.CANCELLED),
    (RunStatus.CANCELLING, RunStatus.COMPLETED),
    (RunStatus.CANCELLING, RunStatus.FAILED),
}


@pytest.mark.parametrize("from_status,to_status", sorted(LEGAL_TRANSITIONS))
def test_legal_transitions_allowed(from_status: RunStatus, to_status: RunStatus) -> None:
    assert can_transition(from_status, to_status) is True


@pytest.mark.parametrize(
    "from_status,to_status",
    [
        pair
        for pair in itertools.product(ALL_STATUSES, ALL_STATUSES)
        if pair not in LEGAL_TRANSITIONS
    ],
)
def test_every_other_pair_rejected(from_status: RunStatus, to_status: RunStatus) -> None:
    assert can_transition(from_status, to_status) is False


@pytest.mark.parametrize(
    "status", [RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED]
)
def test_terminal_statuses_reject_all_outgoing_transitions(status: RunStatus) -> None:
    assert is_terminal(status) is True
    for candidate in ALL_STATUSES:
        assert can_transition(status, candidate) is False


@pytest.mark.parametrize(
    "status", [RunStatus.PENDING, RunStatus.RUNNING, RunStatus.CANCELLING]
)
def test_non_terminal_statuses_are_not_terminal(status: RunStatus) -> None:
    assert is_terminal(status) is False
