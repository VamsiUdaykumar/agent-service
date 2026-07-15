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
import secrets
from datetime import UTC, datetime
from typing import Any

from app.domain.profiles import PROFILES, AgentProfile
from app.domain.status import RunStatus
from app.persistence.ids import new_run_id
from app.persistence.models import RunPage, RunRecord, StepRecord
from app.persistence.repository import Repository
from app.runner.execute import CancelSignal, execute_run
from app.services.errors import UnknownAgentError
from app.telemetry.ids import generate_trace_id

_MAX_SERVER_GENERATED_SEED = 2**63 - 1


class RunService:
    def __init__(self, repository: Repository, sim_speed: float) -> None:
        self._repository = repository
        self._sim_speed = sim_speed
        # In-process registry: lets a future cancel endpoint (Milestone 5)
        # signal the running task promptly. Never the source of truth for
        # cancellation state — that's the persisted `cancelling` status.
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
                await self._repository.append_event(event)
        finally:
            self._tasks.pop(run_id, None)
            self._cancel_signals.pop(run_id, None)

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
