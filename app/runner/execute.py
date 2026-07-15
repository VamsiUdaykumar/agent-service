"""The runner's execution interface: an async generator that walks a seeded
step plan and yields domain events. No HTTP, no persistence import — the
service layer (Milestone 4) consumes this and persists whatever it yields.

`event.sequence` on every yielded event is a placeholder (see
`Repository.append_event`'s contract, app/persistence/repository.py): the
runner has no way to know the true sequence number (RunCreated already
claimed sequence 1 before the runner even starts), so it doesn't try.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from app.domain.errors import RunError, RunErrorCode
from app.domain.events import (
    Event,
    RunCompleted,
    RunFailed,
    RunStarted,
    StepCompleted,
    StepFailed,
    StepRetried,
    StepStarted,
)
from app.domain.profiles import AgentProfile
from app.domain.status import StepType
from app.runner.plan import PlannedStep, generate_step_plan
from app.runner.rng import make_rng
from app.runner.simulate import backoff_delay_ms, sample_latency_ms, sample_tokens_and_cost

# 1 initial attempt + 2 retries. A retryable failure still failing at the
# 3rd attempt is escalated to a terminal, non-retryable one — otherwise an
# unlucky RNG draw could retry forever.
MAX_ATTEMPTS = 3

_PLACEHOLDER_SEQUENCE = 1


class CancelSignal:
    """In-memory, per-run cancellation flag checked at (top-level) step
    boundaries. Never the source of truth — the persisted `cancelling`
    status (Milestone 5) is authoritative; this just lets a running task
    notice promptly instead of waiting for the next store poll.
    """

    def __init__(self) -> None:
        self._event = asyncio.Event()

    def set(self) -> None:
        self._event.set()

    def is_set(self) -> bool:
        return self._event.is_set()


class _RunAborted(Exception):
    """Internal control-flow signal: a non-retryable failure occurred."""

    def __init__(self, error: RunError) -> None:
        self.error = error


class _Totals:
    __slots__ = ("tokens_in", "tokens_out", "cost_usd", "duration_ms")

    def __init__(self) -> None:
        self.tokens_in = 0
        self.tokens_out = 0
        self.cost_usd = 0.0
        self.duration_ms = 0

    def add(
        self,
        tokens_in: int | None,
        tokens_out: int | None,
        cost_usd: float | None,
        duration_ms: int = 0,
    ) -> None:
        self.tokens_in += tokens_in or 0
        self.tokens_out += tokens_out or 0
        self.cost_usd += cost_usd or 0.0
        self.duration_ms += duration_ms

    def merge(self, other: _Totals) -> None:
        self.tokens_in += other.tokens_in
        self.tokens_out += other.tokens_out
        self.cost_usd += other.cost_usd
        self.duration_ms += other.duration_ms


def _now() -> datetime:
    return datetime.now(UTC)


async def execute_run(
    *,
    run_id: str,
    agent_id: str,
    profile: AgentProfile,
    seed: int,
    input: dict[str, Any],
    sim_speed: float,
    cancel_signal: CancelSignal | None = None,
) -> AsyncIterator[Event]:
    """Behavior is a pure function of `(agent_id, seed, input)`: every random
    decision comes from the recipe-seeded RNG, never the wall clock. Two
    calls with the same recipe yield byte-identical events (timestamps
    excepted) — see M3.T7's determinism test.
    """
    rng = make_rng(agent_id, seed, input)
    plan = generate_step_plan(profile, rng)
    totals = _Totals()

    yield RunStarted(run_id=run_id, sequence=_PLACEHOLDER_SEQUENCE, occurred_at=_now())

    try:
        for step in plan:
            if cancel_signal is not None and cancel_signal.is_set():
                return
            async for event in _execute_step(
                run_id, step, rng, profile, sim_speed, totals, parent_step_id=None
            ):
                yield event
    except _RunAborted as aborted:
        yield RunFailed(
            run_id=run_id,
            sequence=_PLACEHOLDER_SEQUENCE,
            occurred_at=_now(),
            error=aborted.error,
            tokens_in=totals.tokens_in,
            tokens_out=totals.tokens_out,
            cost_usd=totals.cost_usd,
            duration_ms=totals.duration_ms,
        )
        return

    yield RunCompleted(
        run_id=run_id,
        sequence=_PLACEHOLDER_SEQUENCE,
        occurred_at=_now(),
        tokens_in=totals.tokens_in,
        tokens_out=totals.tokens_out,
        cost_usd=totals.cost_usd,
        duration_ms=totals.duration_ms,
    )


async def _execute_step(
    run_id: str,
    step: PlannedStep,
    rng: random.Random,
    profile: AgentProfile,
    sim_speed: float,
    totals: _Totals,
    *,
    parent_step_id: str | None,
) -> AsyncIterator[Event]:
    attempt = 1
    while True:
        yield StepStarted(
            run_id=run_id,
            sequence=_PLACEHOLDER_SEQUENCE,
            occurred_at=_now(),
            step_id=step.step_id,
            step_type=step.step_type,
            attempt=attempt,
            parent_step_id=parent_step_id,
        )

        duration_ms = sample_latency_ms(step.step_type, rng)
        await asyncio.sleep((duration_ms / 1000) / sim_speed)
        tokens_in, tokens_out, cost, model_name = sample_tokens_and_cost(step.step_type, rng)

        # Two independent draws every attempt, in a fixed order, regardless
        # of outcome — that's what makes replay with the same seed exact.
        is_non_retryable = rng.random() < profile.non_retryable_rate
        is_retryable_failure = rng.random() < profile.fail_rate
        exhausted = attempt >= MAX_ATTEMPTS

        if is_non_retryable or (is_retryable_failure and exhausted):
            totals.add(tokens_in, tokens_out, cost, duration_ms)
            error = RunError(
                code=RunErrorCode.STEP_FAILED,
                message=(
                    "step failed (non-retryable)"
                    if is_non_retryable
                    else "step failed after exhausting retries"
                ),
                retryable=False,
            )
            yield StepFailed(
                run_id=run_id,
                sequence=_PLACEHOLDER_SEQUENCE,
                occurred_at=_now(),
                step_id=step.step_id,
                attempt=attempt,
                error=error,
                duration_ms=duration_ms,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=cost,
                model_name=model_name,
            )
            raise _RunAborted(error)

        if is_retryable_failure:
            totals.add(tokens_in, tokens_out, cost, duration_ms)
            error = RunError(
                code=RunErrorCode.STEP_FAILED, message="step failed, retrying", retryable=True
            )
            yield StepFailed(
                run_id=run_id,
                sequence=_PLACEHOLDER_SEQUENCE,
                occurred_at=_now(),
                step_id=step.step_id,
                attempt=attempt,
                error=error,
                duration_ms=duration_ms,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=cost,
                model_name=model_name,
            )
            delay_ms = backoff_delay_ms(attempt)
            yield StepRetried(
                run_id=run_id,
                sequence=_PLACEHOLDER_SEQUENCE,
                occurred_at=_now(),
                step_id=step.step_id,
                next_attempt=attempt + 1,
                delay_ms=delay_ms,
            )
            await asyncio.sleep((delay_ms / 1000) / sim_speed)
            totals.duration_ms += delay_ms
            attempt += 1
            continue

        if step.step_type is StepType.SUB_AGENT:
            child_totals = _Totals()
            try:
                for child in step.children:
                    async for event in _execute_step(
                        run_id, child, rng, profile, sim_speed, child_totals,
                        parent_step_id=step.step_id,
                    ):
                        yield event
            finally:
                totals.merge(child_totals)
            totals.duration_ms += duration_ms
            yield StepCompleted(
                run_id=run_id,
                sequence=_PLACEHOLDER_SEQUENCE,
                occurred_at=_now(),
                step_id=step.step_id,
                attempt=attempt,
                tokens_in=child_totals.tokens_in or None,
                tokens_out=child_totals.tokens_out or None,
                cost_usd=child_totals.cost_usd or None,
                duration_ms=duration_ms,
            )
        else:
            totals.add(tokens_in, tokens_out, cost, duration_ms)
            yield StepCompleted(
                run_id=run_id,
                sequence=_PLACEHOLDER_SEQUENCE,
                occurred_at=_now(),
                step_id=step.step_id,
                attempt=attempt,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=cost,
                duration_ms=duration_ms,
                model_name=model_name,
            )
        return
