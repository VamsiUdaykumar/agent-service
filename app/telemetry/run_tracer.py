"""Per-run span-tree + metric bookkeeping. `on_event` is the single
instrumentation point M7.T5 asks for: each event yielded by the runner
drives exactly one call here, which opens/closes the right spans and
records the right metrics. One `RunTracer` instance per run, lives exactly
as long as `RunService._execute`'s coroutine (PRD §3.4).
"""

from __future__ import annotations

from opentelemetry.trace import Span, Status, StatusCode, Tracer, set_span_in_context

from app.domain.errors import RunError
from app.domain.events import (
    Event,
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunStarted,
    StepCompleted,
    StepFailed,
    StepRetried,
    StepStarted,
)
from app.domain.status import StepType
from app.telemetry.metrics import Metrics
from app.telemetry.spans import (
    record_error,
    set_cost_attribute,
    set_genai_attributes,
    start_attempt_span,
    start_run_span,
    start_step_span,
)


class RunTracer:
    def __init__(
        self, tracer: Tracer, metrics: Metrics, *, run_id: str, agent_id: str, trace_id: str
    ) -> None:
        self._tracer = tracer
        self._metrics = metrics
        self._run_id = run_id
        self._agent_id = agent_id
        self._trace_id = trace_id
        self._root_span: Span | None = None
        self._step_spans: dict[str, Span] = {}
        self._attempt_spans: dict[str, Span] = {}
        self._step_types: dict[str, str] = {}

    def on_event(self, event: Event) -> None:
        if isinstance(event, RunStarted):
            self._root_span = start_run_span(
                self._tracer, run_id=self._run_id, agent_id=self._agent_id, trace_id=self._trace_id
            )
        elif isinstance(event, StepStarted):
            self._on_step_started(event)
        elif isinstance(event, StepCompleted):
            self._on_step_completed(event)
        elif isinstance(event, StepFailed):
            self._on_step_failed(event)
        elif isinstance(event, StepRetried):
            pass  # no span action — the failed attempt's span already closed in _on_step_failed
        elif isinstance(event, RunCompleted | RunFailed | RunCancelled):
            self._on_run_terminal(event)

    def _on_step_started(self, event: StepStarted) -> None:
        if event.step_id not in self._step_spans:
            parent = (
                self._step_spans.get(event.parent_step_id)
                if event.parent_step_id is not None
                else self._root_span
            )
            if parent is None:
                return  # defensive: StepStarted should never precede RunStarted
            self._step_spans[event.step_id] = start_step_span(
                self._tracer,
                parent=parent,
                step_id=event.step_id,
                step_type=event.step_type,
                parent_step_id=event.parent_step_id,
            )
            self._step_types[event.step_id] = event.step_type.value

        step_span = self._step_spans[event.step_id]
        self._attempt_spans[event.step_id] = start_attempt_span(
            self._tracer, parent=step_span, attempt=event.attempt
        )

    def _on_step_completed(self, event: StepCompleted) -> None:
        self._end_attempt_span(
            event.step_id, event.model_name, event.tokens_in, event.tokens_out, event.cost_usd
        )

        step_span = self._step_spans.pop(event.step_id, None)
        if step_span is not None:
            set_genai_attributes(
                step_span,
                model_name=event.model_name,
                tokens_in=event.tokens_in,
                tokens_out=event.tokens_out,
            )
            set_cost_attribute(step_span, event.cost_usd)
            step_span.end()

        step_type = self._step_types.pop(event.step_id, "unknown")
        self._metrics.record_step_executed(
            agent_id=self._agent_id, step_type=step_type, outcome="success"
        )
        # A sub-agent step's totals are the sum of its children's, already
        # metered when each child's own StepCompleted/StepFailed fired —
        # recording them again here would double-count tokens/cost.
        if step_type != StepType.SUB_AGENT.value:
            self._record_tokens_and_cost(event.tokens_in, event.tokens_out, event.cost_usd)

    def _on_step_failed(self, event: StepFailed) -> None:
        self._end_attempt_span(
            event.step_id, event.model_name, event.tokens_in, event.tokens_out, event.cost_usd,
            error=event.error,
        )

        if event.error.retryable:
            outcome = "retry"
            step_type = self._step_types.get(event.step_id, "unknown")
        else:
            outcome = "failure"
            step_type = self._step_types.pop(event.step_id, "unknown")
            step_span = self._step_spans.pop(event.step_id, None)
            if step_span is not None:
                record_error(step_span, event.error)
                step_span.end()

        self._metrics.record_step_executed(
            agent_id=self._agent_id, step_type=step_type, outcome=outcome
        )
        if step_type != StepType.SUB_AGENT.value:
            self._record_tokens_and_cost(event.tokens_in, event.tokens_out, event.cost_usd)

    def _end_attempt_span(
        self,
        step_id: str,
        model_name: str | None,
        tokens_in: int | None,
        tokens_out: int | None,
        cost_usd: float | None,
        *,
        error: RunError | None = None,
    ) -> None:
        attempt_span = self._attempt_spans.pop(step_id, None)
        if attempt_span is None:
            return
        set_genai_attributes(
            attempt_span, model_name=model_name, tokens_in=tokens_in, tokens_out=tokens_out
        )
        set_cost_attribute(attempt_span, cost_usd)
        if error is not None:
            record_error(attempt_span, error)
        attempt_span.end()

    def _on_run_terminal(self, event: RunCompleted | RunFailed | RunCancelled) -> None:
        if isinstance(event, RunCompleted):
            status_label = "completed"
        elif isinstance(event, RunFailed):
            status_label = "failed"
        else:
            status_label = "cancelled"

        duration_context = None
        if self._root_span is not None:
            # Captured before `.end()` — exemplar attachment (M8.T2.1) needs
            # the span's SpanContext, not whether it's still recording.
            duration_context = set_span_in_context(self._root_span)
            if isinstance(event, RunFailed):
                record_error(self._root_span, event.error)
            elif isinstance(event, RunCompleted):
                self._root_span.set_status(Status(StatusCode.OK))
            self._root_span.end()
            self._root_span = None

        self._metrics.record_run_completed(agent_id=self._agent_id, status=status_label)
        self._metrics.record_run_duration(
            agent_id=self._agent_id,
            duration_ms=event.duration_ms,
            context=duration_context,
            trace_id=self._trace_id,
        )

    def _record_tokens_and_cost(
        self, tokens_in: int | None, tokens_out: int | None, cost_usd: float | None
    ) -> None:
        self._metrics.record_tokens_used(
            agent_id=self._agent_id, direction="input", tokens=tokens_in or 0
        )
        self._metrics.record_tokens_used(
            agent_id=self._agent_id, direction="output", tokens=tokens_out or 0
        )
        self._metrics.record_cost(agent_id=self._agent_id, cost_usd=cost_usd or 0.0)
