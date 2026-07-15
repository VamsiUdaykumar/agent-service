"""Cardinality-disciplined metric instruments (PRD §3.4) — deliberately
excludes `run_id` and `metadata.*` labels. Each `record_*` method's
signature only accepts the bounded label set it needs, so a forbidden
high-cardinality label can't be passed even by accident.
"""

from __future__ import annotations

from opentelemetry.context import Context
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

    def record_run_duration(
        self,
        *,
        agent_id: str,
        duration_ms: float,
        context: Context | None = None,
        trace_id: str | None = None,
    ) -> None:
        """`context` should carry the run's root span (captured before it's
        ended) so the SDK's default `TraceBasedExemplarFilter` — which reads
        `trace.get_current_span(context)` — has a sampled span to attach the
        measurement to as an exemplar (M8.T2.1). Our spans are opened via
        `tracer.start_span` rather than `start_as_current_span` (they outlive
        a single `with` block, spanning multiple `on_event` calls), so there
        is never an ambient "current span" for the filter to fall back on —
        the context must be passed explicitly.

        `trace_id`, when given, is attached as an extra measurement attribute
        named `traceID`. It is *not* part of `run.duration`'s configured
        attribute set (see the `View(attribute_keys={"agent_id"})` in
        `app.telemetry.setup.configure_metrics`), so the SDK demotes it to
        the exemplar's `filtered_attributes` instead of promoting it into the
        metric's own (cardinality-bounded) label set — same mechanism as the
        exemplar's native trace/span ID, just under the label name our
        provisioned Grafana Cloud Prometheus data source's (read-only)
        exemplar-to-trace link actually expects. The OTel spec hardcodes the
        *native* exemplar trace-ID label as `trace_id` (see
        `prometheus/otlptranslator`'s `ExemplarTraceIDKey`) — that's not
        something a collector config can rename, so this attribute is the
        only lever we have.
        """
        attributes: dict[str, str] = {"agent_id": agent_id}
        if trace_id is not None:
            attributes["traceID"] = trace_id
        self._run_duration.record(duration_ms, attributes, context=context)

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
