"""`aiosqlite`-backed `Repository` implementation.

Every `append_event` call runs in a single transaction that inserts the
event row and folds it into the `steps`/`runs` projections — all or
nothing. A single connection is shared across the process and every public
method takes one `asyncio.Lock`: `aiosqlite` already serializes operations
on one connection through its own background thread, so this just removes
the dirty-read hazard of a reader observing another coroutine's
not-yet-committed write (PRD §4, §6).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any, Self

import aiosqlite

from app.domain.errors import RunError, RunErrorCode
from app.domain.events import (
    Event,
    RunCancelled,
    RunCancelling,
    RunCompleted,
    RunCreated,
    RunFailed,
    RunStarted,
    StepCompleted,
    StepFailed,
    StepRetried,
    StepStarted,
    parse_event,
)
from app.domain.status import RunStatus, StepStatus, StepType, can_transition, is_terminal
from app.persistence.errors import (
    IllegalTransitionError,
    RunNotFoundError,
    TerminalRunConflictError,
)
from app.persistence.migrations import apply_migrations
from app.persistence.models import RunPage, RunRecord, StepRecord

# Event types that drive a run-level status transition, and the status they
# drive it to. Every other event type (StepStarted/Completed/Failed/Retried)
# only touches the `steps` projection and/or accumulates running totals.
_RUN_STATUS_BY_EVENT: dict[type[Event], RunStatus] = {
    RunStarted: RunStatus.RUNNING,
    RunCancelling: RunStatus.CANCELLING,
    RunCompleted: RunStatus.COMPLETED,
    RunFailed: RunStatus.FAILED,
    RunCancelled: RunStatus.CANCELLED,
}


def _row_to_run_record(row: aiosqlite.Row) -> RunRecord:
    metadata = json.loads(row["metadata"]) if row["metadata"] is not None else None
    error = None
    if row["error_code"] is not None:
        error = RunError(
            code=RunErrorCode(row["error_code"]),
            message=row["error_message"],
            retryable=bool(row["error_retryable"]),
        )
    return RunRecord(
        id=row["id"],
        status=RunStatus(row["status"]),
        agent_id=row["agent_id"],
        seed=row["seed"],
        input=json.loads(row["input"]),
        metadata=metadata,
        tokens_in=row["tokens_in"],
        tokens_out=row["tokens_out"],
        cost_usd=row["cost_usd"],
        trace_id=row["trace_id"],
        error=error,
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _row_to_step_record(row: aiosqlite.Row) -> StepRecord:
    error = None
    if row["last_error_code"] is not None:
        error = RunError(
            code=RunErrorCode(row["last_error_code"]),
            message=row["last_error_message"],
            retryable=bool(row["last_error_retryable"]),
        )
    return StepRecord(
        run_id=row["run_id"],
        step_id=row["step_id"],
        parent_step_id=row["parent_step_id"],
        step_type=StepType(row["step_type"]),
        status=StepStatus(row["status"]),
        attempt=row["attempt"],
        tokens_in=row["tokens_in"],
        tokens_out=row["tokens_out"],
        cost_usd=row["cost_usd"],
        last_error=error,
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


class SqliteRepository:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn
        self._lock = asyncio.Lock()

    @classmethod
    async def connect(cls, db_path: str) -> Self:
        conn = await aiosqlite.connect(db_path)
        conn.row_factory = aiosqlite.Row
        if db_path != ":memory:":
            await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA foreign_keys=ON")
        await apply_migrations(conn)
        return cls(conn)

    async def close(self) -> None:
        await self._conn.close()

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
        created_iso = created_at.isoformat()
        input_json = json.dumps(input, separators=(",", ":"))
        metadata_json = json.dumps(metadata) if metadata is not None else None
        event = RunCreated(
            run_id=run_id,
            sequence=1,
            occurred_at=created_at,
            agent_id=agent_id,
            seed=seed,
            input=input,
            metadata=metadata,
            trace_id=trace_id,
        )
        async with self._lock:
            try:
                await self._conn.execute(
                    "INSERT INTO runs (id, status, agent_id, seed, input, metadata,"
                    " trace_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        run_id,
                        RunStatus.PENDING.value,
                        agent_id,
                        seed,
                        input_json,
                        metadata_json,
                        trace_id,
                        created_iso,
                        created_iso,
                    ),
                )
                await self._conn.execute(
                    "INSERT INTO events (run_id, sequence, event_type, payload_json, occurred_at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (run_id, 1, event.event_type, event.model_dump_json(), created_iso),
                )
                await self._conn.commit()
            except Exception:
                await self._conn.rollback()
                raise

        return RunRecord(
            id=run_id,
            status=RunStatus.PENDING,
            agent_id=agent_id,
            seed=seed,
            input=input,
            metadata=metadata,
            tokens_in=0,
            tokens_out=0,
            cost_usd=0.0,
            trace_id=trace_id,
            error=None,
            created_at=created_at,
            updated_at=created_at,
        )

    async def append_event(self, event: Event) -> Event:
        if isinstance(event, RunCreated):
            raise ValueError("RunCreated is written by create_run, not append_event")

        async with self._lock:
            try:
                cursor = await self._conn.execute(
                    "SELECT status FROM runs WHERE id = ?", (event.run_id,)
                )
                row = await cursor.fetchone()
                if row is None:
                    raise RunNotFoundError(event.run_id)
                current_status = RunStatus(row["status"])
                if is_terminal(current_status):
                    raise TerminalRunConflictError(event.run_id, current_status.value)

                new_status = _RUN_STATUS_BY_EVENT.get(type(event))
                if new_status is not None and not can_transition(current_status, new_status):
                    raise IllegalTransitionError(
                        event.run_id, current_status.value, new_status.value
                    )

                seq_cursor = await self._conn.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 FROM events WHERE run_id = ?",
                    (event.run_id,),
                )
                seq_row = await seq_cursor.fetchone()
                assert seq_row is not None
                next_sequence = seq_row[0]
                persisted = event.model_copy(update={"sequence": next_sequence})

                occurred_iso = persisted.occurred_at.isoformat()
                await self._conn.execute(
                    "INSERT INTO events (run_id, sequence, event_type, payload_json, occurred_at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (
                        persisted.run_id,
                        persisted.sequence,
                        persisted.event_type,
                        persisted.model_dump_json(),
                        occurred_iso,
                    ),
                )
                await self._fold_projection(persisted, occurred_iso)
                await self._conn.commit()
            except Exception:
                await self._conn.rollback()
                raise

        return persisted

    async def _fold_projection(self, event: Event, occurred_iso: str) -> None:
        if isinstance(event, RunStarted):
            await self._conn.execute(
                "UPDATE runs SET status = ?, updated_at = ? WHERE id = ?",
                (RunStatus.RUNNING.value, occurred_iso, event.run_id),
            )
        elif isinstance(event, RunCancelling):
            await self._conn.execute(
                "UPDATE runs SET status = ?, updated_at = ? WHERE id = ?",
                (RunStatus.CANCELLING.value, occurred_iso, event.run_id),
            )
        elif isinstance(event, StepStarted):
            await self._conn.execute(
                """
                INSERT INTO steps (run_id, step_id, parent_step_id, step_type, status,
                                    attempt, tokens_in, tokens_out, cost_usd,
                                    created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 0, 0, 0, ?, ?)
                ON CONFLICT (run_id, step_id) DO UPDATE SET
                    status = excluded.status,
                    attempt = excluded.attempt,
                    updated_at = excluded.updated_at
                """,
                (
                    event.run_id,
                    event.step_id,
                    event.parent_step_id,
                    event.step_type.value,
                    StepStatus.RUNNING.value,
                    event.attempt,
                    occurred_iso,
                    occurred_iso,
                ),
            )
        elif isinstance(event, StepCompleted):
            tokens_in, tokens_out, cost_usd = (
                event.tokens_in or 0,
                event.tokens_out or 0,
                event.cost_usd or 0.0,
            )
            await self._conn.execute(
                """
                UPDATE steps SET status = ?, attempt = ?, tokens_in = tokens_in + ?,
                                  tokens_out = tokens_out + ?, cost_usd = cost_usd + ?,
                                  updated_at = ?
                WHERE run_id = ? AND step_id = ?
                """,
                (
                    StepStatus.COMPLETED.value,
                    event.attempt,
                    tokens_in,
                    tokens_out,
                    cost_usd,
                    occurred_iso,
                    event.run_id,
                    event.step_id,
                ),
            )
            await self._accumulate_run_totals(
                event.run_id, tokens_in, tokens_out, cost_usd, occurred_iso
            )
        elif isinstance(event, StepFailed):
            tokens_in, tokens_out, cost_usd = (
                event.tokens_in or 0,
                event.tokens_out or 0,
                event.cost_usd or 0.0,
            )
            await self._conn.execute(
                """
                UPDATE steps SET status = ?, attempt = ?, tokens_in = tokens_in + ?,
                                  tokens_out = tokens_out + ?, cost_usd = cost_usd + ?,
                                  last_error_code = ?, last_error_message = ?,
                                  last_error_retryable = ?, updated_at = ?
                WHERE run_id = ? AND step_id = ?
                """,
                (
                    StepStatus.FAILED.value,
                    event.attempt,
                    tokens_in,
                    tokens_out,
                    cost_usd,
                    event.error.code.value,
                    event.error.message,
                    int(event.error.retryable),
                    occurred_iso,
                    event.run_id,
                    event.step_id,
                ),
            )
            await self._accumulate_run_totals(
                event.run_id, tokens_in, tokens_out, cost_usd, occurred_iso
            )
        elif isinstance(event, StepRetried):
            # Informational only — logged to the event stream; the next
            # StepStarted flips the step back to `running` with the new attempt.
            await self._conn.execute(
                "UPDATE runs SET updated_at = ? WHERE id = ?", (occurred_iso, event.run_id)
            )
        elif isinstance(event, RunCompleted):
            await self._conn.execute(
                """
                UPDATE runs SET status = ?, tokens_in = ?, tokens_out = ?, cost_usd = ?,
                                 updated_at = ?
                WHERE id = ?
                """,
                (
                    RunStatus.COMPLETED.value,
                    event.tokens_in,
                    event.tokens_out,
                    event.cost_usd,
                    occurred_iso,
                    event.run_id,
                ),
            )
        elif isinstance(event, RunFailed):
            await self._conn.execute(
                """
                UPDATE runs SET status = ?, tokens_in = ?, tokens_out = ?, cost_usd = ?,
                                 error_code = ?, error_message = ?, error_retryable = ?,
                                 updated_at = ?
                WHERE id = ?
                """,
                (
                    RunStatus.FAILED.value,
                    event.tokens_in,
                    event.tokens_out,
                    event.cost_usd,
                    event.error.code.value,
                    event.error.message,
                    int(event.error.retryable),
                    occurred_iso,
                    event.run_id,
                ),
            )
        elif isinstance(event, RunCancelled):
            await self._conn.execute(
                """
                UPDATE runs SET status = ?, tokens_in = ?, tokens_out = ?, cost_usd = ?,
                                 updated_at = ?
                WHERE id = ?
                """,
                (
                    RunStatus.CANCELLED.value,
                    event.tokens_in,
                    event.tokens_out,
                    event.cost_usd,
                    occurred_iso,
                    event.run_id,
                ),
            )
        else:  # pragma: no cover - exhaustive by construction of the Event union
            raise AssertionError(f"unhandled event type: {type(event)!r}")

    async def _accumulate_run_totals(
        self, run_id: str, tokens_in: int, tokens_out: int, cost_usd: float, occurred_iso: str
    ) -> None:
        await self._conn.execute(
            """
            UPDATE runs SET tokens_in = tokens_in + ?, tokens_out = tokens_out + ?,
                             cost_usd = cost_usd + ?, updated_at = ?
            WHERE id = ?
            """,
            (tokens_in, tokens_out, cost_usd, occurred_iso, run_id),
        )

    async def get_run(self, run_id: str) -> RunRecord | None:
        async with self._lock:
            cursor = await self._conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,))
            row = await cursor.fetchone()
        return _row_to_run_record(row) if row is not None else None

    async def get_steps(self, run_id: str) -> list[StepRecord]:
        async with self._lock:
            cursor = await self._conn.execute(
                "SELECT * FROM steps WHERE run_id = ? ORDER BY created_at, step_id", (run_id,)
            )
            rows = await cursor.fetchall()
        return [_row_to_step_record(row) for row in rows]

    async def get_events_from(self, run_id: str, after_sequence: int) -> list[Event]:
        async with self._lock:
            cursor = await self._conn.execute(
                "SELECT payload_json FROM events WHERE run_id = ? AND sequence > ?"
                " ORDER BY sequence",
                (run_id, after_sequence),
            )
            rows = await cursor.fetchall()
        return [parse_event(json.loads(row["payload_json"])) for row in rows]

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
        clauses: list[str] = []
        params: list[object] = []
        if cursor is not None:
            clauses.append("id < ?")
            params.append(cursor)
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        if agent_id is not None:
            clauses.append("agent_id = ?")
            params.append(agent_id)
        if created_after is not None:
            clauses.append("created_at > ?")
            params.append(created_after.isoformat())
        if created_before is not None:
            clauses.append("created_at < ?")
            params.append(created_before.isoformat())
        for key, value in (metadata_filters or {}).items():
            clauses.append("json_extract(metadata, ?) = ?")
            params.append(f"$.{key}")
            params.append(value)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit + 1)

        async with self._lock:
            db_cursor = await self._conn.execute(
                f"SELECT * FROM runs {where} ORDER BY id DESC LIMIT ?", params
            )
            rows = list(await db_cursor.fetchall())

        has_more = len(rows) > limit
        page_rows = rows[:limit]
        data = [_row_to_run_record(row) for row in page_rows]
        next_cursor = data[-1].id if has_more and data else None
        return RunPage(data=data, has_more=has_more, next_cursor=next_cursor)
