from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode, format_trace_id

from app.domain.errors import RunError, RunErrorCode
from app.domain.status import StepType
from app.telemetry.spans import (
    record_error,
    set_cost_attribute,
    set_genai_attributes,
    start_attempt_span,
    start_run_span,
    start_step_span,
)


def _tracer():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider.get_tracer("test"), exporter


def test_start_run_span_adopts_the_given_trace_id() -> None:
    tracer, exporter = _tracer()
    trace_id = "a" * 32
    span = start_run_span(tracer, run_id="run-1", agent_id="agent-simple", trace_id=trace_id)
    span.end()

    (finished,) = exporter.get_finished_spans()
    assert format_trace_id(finished.context.trace_id) == trace_id
    assert finished.attributes is not None
    assert finished.attributes["stackai.run.id"] == "run-1"
    assert finished.attributes["stackai.agent_id"] == "agent-simple"


def test_start_step_span_sets_attributes_and_nests_under_parent() -> None:
    tracer, exporter = _tracer()
    root = start_run_span(tracer, run_id="run-1", agent_id="agent-simple", trace_id="b" * 32)
    step = start_step_span(
        tracer, parent=root, step_id="step-1", step_type=StepType.MODEL_CALL, parent_step_id=None
    )
    step.end()
    root.end()

    finished = {s.name: s for s in exporter.get_finished_spans()}
    step_span = finished["model_call"]
    assert step_span.attributes is not None
    assert step_span.attributes["stackai.step_id"] == "step-1"
    assert step_span.attributes["stackai.step_type"] == "model_call"
    assert step_span.parent is not None
    assert step_span.parent.span_id == root.context.span_id


def test_start_attempt_span_sets_attempt_attribute() -> None:
    tracer, exporter = _tracer()
    root = start_run_span(tracer, run_id="run-1", agent_id="agent-simple", trace_id="c" * 32)
    step = start_step_span(
        tracer, parent=root, step_id="step-1", step_type=StepType.TOOL_CALL, parent_step_id=None
    )
    attempt = start_attempt_span(tracer, parent=step, attempt=2)
    attempt.end()
    step.end()
    root.end()

    finished = {s.name: s for s in exporter.get_finished_spans()}
    attempt_span = finished["attempt-2"]
    assert attempt_span.attributes is not None
    assert attempt_span.attributes["stackai.attempt"] == 2
    assert attempt_span.parent is not None
    assert attempt_span.parent.span_id == step.context.span_id


def test_set_genai_attributes_skips_none_values() -> None:
    tracer, exporter = _tracer()
    span = tracer.start_span("x")
    set_genai_attributes(span, model_name=None, tokens_in=10, tokens_out=None)
    span.end()

    (finished,) = exporter.get_finished_spans()
    assert finished.attributes is not None
    assert "gen_ai.request.model" not in finished.attributes
    assert finished.attributes["gen_ai.usage.input_tokens"] == 10
    assert "gen_ai.usage.output_tokens" not in finished.attributes


def test_set_cost_attribute_skips_none() -> None:
    tracer, exporter = _tracer()
    span = tracer.start_span("x")
    set_cost_attribute(span, None)
    span.end()
    (finished,) = exporter.get_finished_spans()
    assert finished.attributes is not None
    assert "stackai.cost_usd" not in finished.attributes


def test_record_error_sets_error_status_and_attributes() -> None:
    tracer, exporter = _tracer()
    span = tracer.start_span("x")
    error = RunError(code=RunErrorCode.STEP_FAILED, message="boom", retryable=True)
    record_error(span, error)
    span.end()

    (finished,) = exporter.get_finished_spans()
    assert finished.status.status_code is StatusCode.ERROR
    assert finished.attributes is not None
    assert finished.attributes["stackai.error.code"] == "step_failed"
    assert finished.attributes["stackai.error.retryable"] is True
    assert len(finished.events) == 1
    assert finished.events[0].name == "error"
