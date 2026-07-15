from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import format_trace_id, set_span_in_context

from app.telemetry.metrics import Metrics
from app.telemetry.setup import RUN_DURATION_VIEW


def _metrics() -> tuple[Metrics, InMemoryMetricReader]:
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    return Metrics(provider.get_meter("test")), reader


def _metrics_with_run_duration_view() -> tuple[Metrics, InMemoryMetricReader]:
    """Mirrors `configure_metrics`'s real `MeterProvider` wiring (M8.T2.1) —
    needed to test the `traceID` exemplar attribute's cardinality-safety,
    since that only holds with the view in place.
    """
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader], views=[RUN_DURATION_VIEW])
    return Metrics(provider.get_meter("test")), reader


def _points(reader: InMemoryMetricReader) -> list[tuple[str, dict]]:
    data = reader.get_metrics_data()
    if data is None:
        return []
    return [
        (metric.name, dict(dp.attributes))
        for rm in data.resource_metrics
        for sm in rm.scope_metrics
        for metric in sm.metrics
        for dp in metric.data.data_points
    ]


def test_record_run_completed_labels_are_bounded() -> None:
    metrics, reader = _metrics()
    metrics.record_run_completed(agent_id="agent-simple", status="completed")
    (name, attrs), = _points(reader)
    assert name == "runs.completed"
    assert attrs == {"agent_id": "agent-simple", "status": "completed"}


def test_record_run_duration_labels_are_bounded() -> None:
    metrics, reader = _metrics()
    metrics.record_run_duration(agent_id="agent-simple", duration_ms=42.0)
    (name, attrs), = _points(reader)
    assert name == "run.duration"
    assert attrs == {"agent_id": "agent-simple"}


def test_record_run_duration_without_context_records_no_exemplar() -> None:
    """No span context passed (e.g. root span already gone) — nothing to
    attach an exemplar to. Confirms the default arg doesn't crash."""
    metrics, reader = _metrics()
    metrics.record_run_duration(agent_id="agent-simple", duration_ms=42.0)
    data = reader.get_metrics_data()
    assert data is not None
    dp = data.resource_metrics[0].scope_metrics[0].metrics[0].data.data_points[0]
    assert list(dp.exemplars) == []


def test_record_run_duration_with_sampled_span_context_attaches_exemplar() -> None:
    """M8.T2.1: passing the run's root-span context (captured before the
    span ends, per `RunTracer._on_run_terminal`) is what lets the SDK's
    default `TraceBasedExemplarFilter` attach an exemplar — the mechanism
    the p95 dashboard panel's click-through-to-trace relies on.
    """
    metrics, reader = _metrics()
    tracer = TracerProvider().get_tracer("test")
    span = tracer.start_span("run")
    context = set_span_in_context(span)

    metrics.record_run_duration(agent_id="agent-simple", duration_ms=42.0, context=context)
    span.end()

    data = reader.get_metrics_data()
    assert data is not None
    dp = data.resource_metrics[0].scope_metrics[0].metrics[0].data.data_points[0]
    (exemplar,) = dp.exemplars
    assert exemplar.trace_id == span.get_span_context().trace_id
    assert exemplar.span_id == span.get_span_context().span_id
    assert exemplar.value == 42.0


def test_record_run_duration_trace_id_becomes_traceID_exemplar_attribute_only() -> None:
    """M8.T2.1 gate finding: the provisioned Grafana Cloud Prometheus data
    source is read-only and its exemplar-to-trace link expects a `traceID`
    label — but the OTel spec hardcodes the *native* exemplar trace-ID label
    as `trace_id` (`prometheus/otlptranslator`'s `ExemplarTraceIDKey`), which
    no collector config can rename. So `trace_id=` attaches a redundant
    `traceID` attribute instead. It must land only on the exemplar's
    `filtered_attributes` (via the `run.duration` view's `attribute_keys`),
    never on the metric's own data-point labels — otherwise every run would
    mint its own unbounded `run.duration{traceID=...}` series.
    """
    metrics, reader = _metrics_with_run_duration_view()
    tracer = TracerProvider().get_tracer("test")
    span = tracer.start_span("run")
    context = set_span_in_context(span)
    trace_id_hex = format_trace_id(span.get_span_context().trace_id)

    metrics.record_run_duration(
        agent_id="agent-simple", duration_ms=42.0, context=context, trace_id=trace_id_hex
    )
    span.end()

    data = reader.get_metrics_data()
    assert data is not None
    dp = data.resource_metrics[0].scope_metrics[0].metrics[0].data.data_points[0]
    assert dict(dp.attributes) == {"agent_id": "agent-simple"}  # cardinality untouched

    (exemplar,) = dp.exemplars
    assert dict(exemplar.filtered_attributes) == {"traceID": trace_id_hex}


def test_record_tokens_used_labels_are_bounded() -> None:
    metrics, reader = _metrics()
    metrics.record_tokens_used(agent_id="agent-simple", direction="input", tokens=100)
    (name, attrs), = _points(reader)
    assert name == "tokens.used"
    assert attrs == {"agent_id": "agent-simple", "direction": "input"}


def test_record_tokens_used_skips_zero() -> None:
    metrics, reader = _metrics()
    metrics.record_tokens_used(agent_id="agent-simple", direction="input", tokens=0)
    assert _points(reader) == []


def test_record_cost_labels_are_bounded() -> None:
    metrics, reader = _metrics()
    metrics.record_cost(agent_id="agent-simple", cost_usd=0.05)
    (name, attrs), = _points(reader)
    assert name == "cost.usd"
    assert attrs == {"agent_id": "agent-simple"}


def test_record_cost_skips_zero() -> None:
    metrics, reader = _metrics()
    metrics.record_cost(agent_id="agent-simple", cost_usd=0.0)
    assert _points(reader) == []


def test_record_step_executed_labels_are_bounded() -> None:
    metrics, reader = _metrics()
    metrics.record_step_executed(agent_id="agent-simple", step_type="model_call", outcome="success")
    (name, attrs), = _points(reader)
    assert name == "steps.executed"
    assert attrs == {"agent_id": "agent-simple", "step_type": "model_call", "outcome": "success"}


def test_no_instrument_accepts_run_id_or_metadata_labels() -> None:
    """M7.T4.2: every `record_*` signature is the enforcement — this drives
    each one and confirms none of the resulting label sets contain `run_id`
    or a `metadata.*` key, since the methods have no parameter for either.
    """
    metrics, reader = _metrics()
    metrics.record_run_completed(agent_id="a", status="completed")
    metrics.record_run_duration(agent_id="a", duration_ms=1.0)
    metrics.record_tokens_used(agent_id="a", direction="input", tokens=1)
    metrics.record_cost(agent_id="a", cost_usd=1.0)
    metrics.record_step_executed(agent_id="a", step_type="model_call", outcome="success")

    for _, attrs in _points(reader):
        assert "run_id" not in attrs
        assert not any(key.startswith("metadata.") for key in attrs)
