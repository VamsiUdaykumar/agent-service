from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from app.telemetry.metrics import Metrics


def _metrics() -> tuple[Metrics, InMemoryMetricReader]:
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
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
