"""Startup recovery: a run left `pending`/`running`/`cancelling` by a prior
process that died is resolved to `failed` (`interrupted_by_restart`) through
the normal `append_event` path, so it's indistinguishable from any other
terminal write (PRD §3.1).
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain.errors import RunError, RunErrorCode
from app.domain.events import RunFailed
from app.domain.status import RunStatus
from app.persistence.repository import Repository

_NON_TERMINAL_STATUSES = (RunStatus.PENDING, RunStatus.RUNNING, RunStatus.CANCELLING)


async def recover_orphaned_runs(repository: Repository) -> int:
    """Resolve every orphaned non-terminal run to `failed`. Returns the count."""
    recovered = 0
    for run_status in _NON_TERMINAL_STATUSES:
        cursor: str | None = None
        while True:
            page = await repository.list_runs(status=run_status, cursor=cursor, limit=100)
            for record in page.data:
                await repository.append_event(
                    RunFailed(
                        run_id=record.id,
                        sequence=1,
                        occurred_at=datetime.now(UTC),
                        error=RunError(
                            code=RunErrorCode.INTERRUPTED_BY_RESTART,
                            message="run was interrupted by a server restart",
                            retryable=False,
                        ),
                        tokens_in=record.tokens_in,
                        tokens_out=record.tokens_out,
                        cost_usd=record.cost_usd,
                        duration_ms=0,
                    )
                )
                recovered += 1
            if not page.has_more:
                break
            cursor = page.next_cursor
    return recovered
