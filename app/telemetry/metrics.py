"""Cardinality-disciplined metric instruments (PRD §3.4) — deliberately
excludes `run_id` and `metadata.*` labels. Each `record_*` method's
signature only accepts the bounded label set it needs, so a forbidden
high-cardinality label can't be passed even by accident.
"""

from __future__ import annotations

from opentelemetry.metrics import Meter


class Metrics:
    def __init__(self, meter: Meter) -> None:
        self._runs_completed = meter.create_counter(
            "runs.completed", unit="1", description="Runs reaching a terminal status"
        )
        self._run_duration = meter.create_histogram(
            "run.duration", unit="ms", description="Run duration from start to terminal status"
        )
        self._tokens_used = meter.create_counter(
            "tokens.used", unit="1", description="Tokens consumed"
        )
        self._cost_usd = meter.create_counter(
            "cost.usd", unit="1", description="Simulated cost accrued"
        )
        self._steps_executed = meter.create_counter(
            "steps.executed", unit="1", description="Step attempts executed"
        )

    def record_run_completed(self, *, agent_id: str, status: str) -> None:
        self._runs_completed.add(1, {"agent_id": agent_id, "status": status})

    def record_run_duration(self, *, agent_id: str, duration_ms: float) -> None:
        self._run_duration.record(duration_ms, {"agent_id": agent_id})

    def record_tokens_used(self, *, agent_id: str, direction: str, tokens: int) -> None:
        if tokens <= 0:
            return
        self._tokens_used.add(tokens, {"agent_id": agent_id, "direction": direction})

    def record_cost(self, *, agent_id: str, cost_usd: float) -> None:
        if cost_usd <= 0:
            return
        self._cost_usd.add(cost_usd, {"agent_id": agent_id})

    def record_step_executed(self, *, agent_id: str, step_type: str, outcome: str) -> None:
        self._steps_executed.add(
            1, {"step_type": step_type, "outcome": outcome, "agent_id": agent_id}
        )
