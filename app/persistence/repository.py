"""The documented seam to swap SQLite for Postgres later (PRD §4).

No call site outside `app.persistence` should import `aiosqlite` directly.
Everything else — services, API — talks to a `Repository`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from app.domain.events import Event
from app.domain.status import RunStatus
from app.persistence.models import RunPage, RunRecord, StepRecord


class Repository(Protocol):
    async def create_run(
        self,
        *,
        run_id: str,
        agent_id: str,
        seed: int,
        input: dict[str, Any],
        metadata: dict[str, str] | None,
        trace_id: str,
        created_at: datetime,
    ) -> RunRecord:
        """Durable-first: insert the `pending` row and its `RunCreated` event
        (sequence 1) in one transaction. Returns the persisted envelope —
        the caller may treat the return as proof of durability before
        spawning execution (PRD §3.1).
        """
        ...

    async def append_event(self, event: Event) -> Event:
        """Atomically (a) insert the event row, (b) fold it into the `steps`
        projection, and (c) fold it into the `runs` projection — all in one
        transaction; a mid-write failure leaves no partial state.

        `event.sequence` on the input is a placeholder and is ignored: the
        repository assigns the authoritative next value as
        `COALESCE(MAX(sequence), 0) + 1` for the run and returns a new event
        carrying it (amendment 3 — the first event of a run is sequence 1).

        Raises `TerminalRunConflictError` if the run is already in a
        terminal status (`completed`, `failed`, `cancelled`) — terminal
        states are immutable, enforced here at the store layer, not only in
        the domain layer. Raises `RunNotFoundError` if the run doesn't
        exist. Raises `IllegalTransitionError` if the event implies a
        run-status transition that `can_transition` rejects.
        """
        ...

    async def get_run(self, run_id: str) -> RunRecord | None:
        """Read-only fold of the `runs` projection. `None` if not found."""
        ...

    async def get_steps(self, run_id: str) -> list[StepRecord]:
        """Read-only fold of the `steps` projection for one run, insertion order."""
        ...

    async def get_events_from(self, run_id: str, after_sequence: int) -> list[Event]:
        """Return events with `sequence > after_sequence`, in sequence order.
        `after_sequence=0` returns the full log from the beginning
        (amendment 3 — sequences are 1-indexed, so 0 is a natural sentinel).
        """
        ...

    async def list_runs(
        self,
        *,
        cursor: str | None = None,
        limit: int = 20,
        status: RunStatus | None = None,
        agent_id: str | None = None,
        metadata_filters: dict[str, str] | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
    ) -> RunPage:
        """Cursor-paginated, sorted `created_at desc` (fixed). The cursor is
        a run's ULID `id` directly, since ULIDs are time-sortable — no
        separate opaque cursor encoding needed. Offset pagination is
        rejected: pages would shift under concurrent writes (PRD §3.3).
        """
        ...

    async def create_run_idempotent(
        self,
        *,
        idempotency_key: str,
        request_hash: str,
        ttl_cutoff: datetime,
        run_id: str,
        agent_id: str,
        seed: int,
        input: dict[str, Any],
        metadata: dict[str, str] | None,
        trace_id: str,
        created_at: datetime,
    ) -> tuple[RunRecord, str]:
        """Reserve `idempotency_key` for `run_id` and, if this call wins the
        reservation, create the run — both in ONE transaction (PRD §3.3).
        The `UNIQUE` constraint on `key` is what decides a concurrent race
        (M6.T3), not application-level locking; doing the reservation and
        the run creation in one transaction is what makes a crash between
        the two structurally impossible — either both survive or neither
        does, so no caller can ever observe a reservation pointing at a
        run that doesn't exist as a result of a crash here.

        Returns `(record, outcome)`:
        - `outcome="created"`: this call won (fresh key, or an existing
          row older than `ttl_cutoff` — lazy 24h expiry, M6.T4, no
          background sweeper needed at this scale) and its run now exists.
        - `outcome="replayed"`: a live existing reservation already
          pointed at another run with a matching `request_hash`; that
          run's current envelope is returned, and no new run was created.

        Raises `IdempotencyConflictError` if a live existing reservation
        has a different `request_hash`.
        """
        ...
