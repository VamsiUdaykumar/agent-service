"""Errors raised by `Repository` implementations.

Caught by the service/API layers and mapped to HTTP statuses (M4.T2.4) —
e.g. `TerminalRunConflictError` -> 409, `RunNotFoundError` -> 404. Kept in
`app.persistence` rather than `app.domain` because they describe a storage
contract violation, not a business rule the domain layer itself defines.
"""

from __future__ import annotations


class RunNotFoundError(Exception):
    def __init__(self, run_id: str) -> None:
        super().__init__(f"run {run_id!r} not found")
        self.run_id = run_id


class TerminalRunConflictError(Exception):
    """Raised when a write targets a run already in a terminal status.

    Enforces "terminal run states are immutable" at the store layer (PRD §2),
    not just via the domain's `can_transition`/`is_terminal` checks.
    """

    def __init__(self, run_id: str, status: str) -> None:
        super().__init__(f"run {run_id!r} is already terminal ({status}); rejecting write")
        self.run_id = run_id
        self.status = status


class IllegalTransitionError(Exception):
    """Raised when an event would drive a run through an illegal status transition."""

    def __init__(self, run_id: str, from_status: str, to_status: str) -> None:
        super().__init__(f"run {run_id!r} cannot transition {from_status} -> {to_status}")
        self.run_id = run_id
        self.from_status = from_status
        self.to_status = to_status


class IdempotencyConflictError(Exception):
    """Raised when an `Idempotency-Key` is reused with a different request body (PRD §3.3).

    Raised by `Repository.create_run_idempotent` itself (not the service
    layer): the conflict is detected inside the same atomic
    reserve-then-create transaction that decides who wins the key.
    """

    def __init__(self, key: str) -> None:
        super().__init__(f"Idempotency-Key {key!r} was already used with a different request body")
        self.key = key
