"""Durable-first run execution: `create_run` persists the run as `pending`
via the repository BEFORE touching the runner at all, then spawns an
`asyncio.Task` that drives the runner and appends each yielded event. This
ordering is what makes "the response never lies" true (PRD §3.1).

Seam: a queue-backed worker would replace the `asyncio.create_task` call
below with an enqueue — everything else (durable-first persist, event
consumption) stays the same (PRD §4).
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from app.domain.errors import RunError, RunErrorCode
from app.domain.events import Event, RunCancelled, RunCancelling, RunCompleted, RunFailed
from app.domain.profiles import PROFILES, AgentProfile
from app.domain.status import RunStatus, is_terminal
from app.persistence.errors import IllegalTransitionError, TerminalRunConflictError
from app.persistence.ids import new_run_id
from app.persistence.models import RunPage, RunRecord, StepRecord
from app.persistence.repository import Repository
from app.runner.execute import CancelSignal, execute_run
from app.services.errors import UnknownAgentError
from app.services.event_bus import RunEventBus
from app.telemetry.ids import generate_trace_id

logger = logging.getLogger(__name__)

_MAX_SERVER_GENERATED_SEED = 2**63 - 1
_HEARTBEAT_INTERVAL_SECONDS = 15.0


def _is_terminal_event(event: Event) -> bool:
    return isinstance(event, RunCompleted | RunFailed | RunCancelled)


class RunService:
    def __init__(self, repository: Repository, sim_speed: float) -> None:
        self._repository = repository
        self._sim_speed = sim_speed
        self._event_bus = RunEventBus()
        # In-process registry: lets the cancel endpoint signal the running
        # task promptly. Never the source of truth for cancellation state —
        # that's the persisted `cancelling` status (amendment 1).
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._cancel_signals: dict[str, CancelSignal] = {}

    async def create_run(
        self,
        *,
        agent_id: str,
        input: dict[str, Any],
        seed: int | None,
        metadata: dict[str, str] | None,
    ) -> RunRecord:
        profile = PROFILES.get(agent_id)
        if profile is None:
            raise UnknownAgentError(agent_id)

        run_id = new_run_id()
        trace_id = generate_trace_id()
        # Server-generated when omitted, so every run ever created is
        # replayable from its recipe alone (PRD §3.3).
        resolved_seed = seed if seed is not None else secrets.randbelow(_MAX_SERVER_GENERATED_SEED)

        record = await self._repository.create_run(
            run_id=run_id,
            agent_id=agent_id,
            seed=resolved_seed,
            input=input,
            metadata=metadata,
            trace_id=trace_id,
            created_at=datetime.now(UTC),
        )

        cancel_signal = CancelSignal()
        self._cancel_signals[run_id] = cancel_signal
        task = asyncio.create_task(
            self._execute(run_id, agent_id, profile, resolved_seed, input, cancel_signal)
        )
        self._tasks[run_id] = task

        return record

    async def _execute(
        self,
        run_id: str,
        agent_id: str,
        profile: AgentProfile,
        seed: int,
        input: dict[str, Any],
        cancel_signal: CancelSignal,
    ) -> None:
        try:
            async for event in execute_run(
                run_id=run_id,
                agent_id=agent_id,
                profile=profile,
                seed=seed,
                input=input,
                sim_speed=self._sim_speed,
                cancel_signal=cancel_signal,
            ):
                persisted = await self._repository.append_event(event)
                self._event_bus.publish(run_id, persisted)
        except Exception:
            logger.exception("unexpected error while executing run %s", run_id)
        finally:
            # The runner itself never emits a terminal event on cancellation
            # (M3.T6.2's checkpoint just stops the generator) — this
            # guarantees one lands regardless of how execution stopped
            # (amendment 1; M5.T6.2). Best-effort: e.g. the repository may
            # already be closed if the process is shutting down.
            try:
                await self._ensure_terminal(run_id)
            except Exception:
                logger.exception("failed to ensure a terminal event for run %s", run_id)
            self._tasks.pop(run_id, None)
            self._cancel_signals.pop(run_id, None)

    async def _ensure_terminal(self, run_id: str) -> None:
        record = await self._repository.get_run(run_id)
        if record is None or is_terminal(record.status):
            return

        event: Event
        if record.status is RunStatus.CANCELLING:
            event = RunCancelled(
                run_id=run_id,
                sequence=1,
                occurred_at=datetime.now(UTC),
                tokens_in=record.tokens_in,
                tokens_out=record.tokens_out,
                cost_usd=record.cost_usd,
                duration_ms=0,
            )
        else:
            event = RunFailed(
                run_id=run_id,
                sequence=1,
                occurred_at=datetime.now(UTC),
                error=RunError(
                    code=RunErrorCode.STEP_FAILED,
                    message="run execution stopped unexpectedly",
                    retryable=False,
                ),
                tokens_in=record.tokens_in,
                tokens_out=record.tokens_out,
                cost_usd=record.cost_usd,
                duration_ms=0,
            )

        try:
            persisted = await self._repository.append_event(event)
        except (TerminalRunConflictError, IllegalTransitionError):
            return  # lost a race with a legitimate terminal write; nothing to do
        self._event_bus.publish(run_id, persisted)

    async def cancel_run(self, run_id: str) -> RunRecord:
        """Persist the `cancelling` transition (durable, via `append_event`)
        before touching the in-process signal — raises `RunNotFoundError` /
        `TerminalRunConflictError` via the repository's own guard, so
        not-found and already-terminal are both handled by one source of
        truth (M5.T5).
        """
        event = RunCancelling(run_id=run_id, sequence=1, occurred_at=datetime.now(UTC))
        persisted = await self._repository.append_event(event)
        self._event_bus.publish(run_id, persisted)

        cancel_signal = self._cancel_signals.get(run_id)
        if cancel_signal is not None:
            cancel_signal.set()

        record = await self._repository.get_run(run_id)
        assert record is not None
        return record

    async def get_run(self, run_id: str) -> RunRecord | None:
        return await self._repository.get_run(run_id)

    async def get_steps(self, run_id: str) -> list[StepRecord]:
        return await self._repository.get_steps(run_id)

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
        return await self._repository.list_runs(
            cursor=cursor,
            limit=limit,
            status=status,
            agent_id=agent_id,
            metadata_filters=metadata_filters,
            created_after=created_after,
            created_before=created_before,
        )

    async def tail_events(
        self,
        run_id: str,
        after_sequence: int,
        *,
        heartbeat_interval: float = _HEARTBEAT_INTERVAL_SECONDS,
    ) -> AsyncIterator[Event | None]:
        """Tail the persisted event log for one run: historical events first,
        then live events via the in-process bus, until a terminal event is
        reached. Yields `None` on idle timeout as a heartbeat signal — never
        touches the runner directly, purely a tail of the store (PRD §3.1).
        """
        last_sequence = after_sequence
        for event in await self._repository.get_events_from(run_id, after_sequence):
            last_sequence = event.sequence
            yield event
            if _is_terminal_event(event):
                return

        run = await self._repository.get_run(run_id)
        if run is not None and is_terminal(run.status):
            return

        queue = self._event_bus.subscribe(run_id)
        try:
            # Catch anything appended in the narrow gap between the historical
            # read above and the subscribe() call just now.
            for event in await self._repository.get_events_from(run_id, last_sequence):
                last_sequence = event.sequence
                yield event
                if _is_terminal_event(event):
                    return

            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=heartbeat_interval)
                except TimeoutError:
                    yield None
                    continue
                if event.sequence <= last_sequence:
                    continue  # already delivered via the catch-up read above
                last_sequence = event.sequence
                yield event
                if _is_terminal_event(event):
                    return
        finally:
            self._event_bus.unsubscribe(run_id, queue)
