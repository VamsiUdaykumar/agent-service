"""M7.T6: in-memory-exporter assertions on the real instrumentation wired
into `RunService` (M7.T5) — root span shape, retry attempts, sub-agent
nesting, error status, and metric emission/cardinality discipline.
"""

from __future__ import annotations

from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode, format_trace_id

from tests.telemetry.conftest import Instrumented, run_to_completion

# Seed 2 / agent-researcher / {"prompt": "x"} is known (brute-force search)
# to produce a run with a retry, a sub-agent step, and a clean completion —
# one run that exercises the whole span-tree shape.
RETRY_AND_SUBAGENT_SEED = 2


def _spans_by_name(exporter: InMemorySpanExporter, name: str) -> list[ReadableSpan]:
    return [s for s in exporter.get_finished_spans() if s.name == name]


def _metric_data_points(reader: InMemoryMetricReader) -> list[tuple[str, dict, object]]:
    points: list[tuple[str, dict, object]] = []
    data = reader.get_metrics_data()
    if data is None:
        return points
    for resource_metrics in data.resource_metrics:
        for scope_metrics in resource_metrics.scope_metrics:
            for metric in scope_metrics.metrics:
                for dp in metric.data.data_points:
                    value = getattr(dp, "value", None)
                    if value is None:
                        value = getattr(dp, "sum", None)
                    points.append((metric.name, dict(dp.attributes), value))
    return points


async def test_root_span_trace_id_matches_persisted_trace_id(
    instrumented: Instrumented,
) -> None:
    """M7.T3.4: confirms the explicit-context technique (M7.T2.1) actually
    works — the API never reads the trace ID back from OTel (PRD §2); this
    just checks the two independently-derived values agree.
    """
    run = await run_to_completion(instrumented, "agent-simple", seed=1)

    roots = _spans_by_name(instrumented.span_exporter, "run")
    assert len(roots) == 1
    root = roots[0]
    assert format_trace_id(root.context.trace_id) == run.trace_id
    assert root.attributes is not None
    assert root.attributes["stackai.run.id"] == run.id
    assert root.attributes["stackai.agent_id"] == "agent-simple"


async def test_span_tree_shape_with_retry_and_sub_agent_nesting(
    instrumented: Instrumented,
) -> None:
    run = await run_to_completion(
        instrumented, "agent-researcher", seed=RETRY_AND_SUBAGENT_SEED
    )
    steps = await instrumented.repository.get_steps(run.id)

    spans = instrumented.span_exporter.get_finished_spans()
    spans_by_id = {s.context.span_id: s for s in spans}

    root = _spans_by_name(instrumented.span_exporter, "run")[0]

    step_spans = [
        s
        for s in spans
        if s.attributes is not None
        and "stackai.step_id" in s.attributes
        and "attempt-" not in s.name
    ]
    assert len(step_spans) == len(steps)

    # Every step span's parent resolves to either the root or another step
    # span (sub-agent nesting, exactly one level) — never dangling.
    for step_span in step_spans:
        assert step_span.parent is not None
        parent_id = step_span.parent.span_id
        assert parent_id == root.context.span_id or parent_id in {
            s.context.span_id for s in step_spans
        }

    # At least one step has more than one attempt span — the retry.
    attempt_spans = [s for s in spans if s.name.startswith("attempt-")]
    attempts_by_parent: dict[int, list[ReadableSpan]] = {}
    for a in attempt_spans:
        assert a.parent is not None
        attempts_by_parent.setdefault(a.parent.span_id, []).append(a)
    assert any(len(v) > 1 for v in attempts_by_parent.values())

    # Each attempt span is distinct (own span_id) and a child of its step.
    for parent_span_id, attempts in attempts_by_parent.items():
        assert parent_span_id in spans_by_id
        assert len({a.context.span_id for a in attempts}) == len(attempts)

    # At least one attempt span carries ERROR status (the retried failure).
    assert any(a.status.status_code is StatusCode.ERROR for a in attempt_spans)

    # Sub-agent nesting: some step span is itself the parent of other step
    # spans (exactly one level, per the plan generator's own guarantee).
    step_span_ids = {s.context.span_id for s in step_spans}
    nested_parents = {
        s.parent.span_id
        for s in step_spans
        if s.parent is not None and s.parent.span_id in step_span_ids
    }
    assert nested_parents, "expected at least one sub-agent step with nested children"


async def test_metrics_emitted_with_correct_labels_and_no_forbidden_cardinality(
    instrumented: Instrumented,
) -> None:
    run = await run_to_completion(instrumented, "agent-simple", seed=1)
    points = _metric_data_points(instrumented.metric_reader)

    names = {name for name, _, _ in points}
    assert names == {
        "runs.completed",
        "run.duration",
        "tokens.used",
        "cost.usd",
        "steps.executed",
    }

    for _, attributes, _ in points:
        assert "run_id" not in attributes
        assert not any(key.startswith("metadata.") for key in attributes)

    runs_completed = [
        (attrs, value) for name, attrs, value in points if name == "runs.completed"
    ]
    assert (
        {"agent_id": "agent-simple", "status": "completed"},
        1,
    ) in runs_completed

    run_duration = [attrs for name, attrs, _ in points if name == "run.duration"]
    assert {"agent_id": "agent-simple"} in run_duration

    tokens_used = {
        (attrs["agent_id"], attrs["direction"])
        for name, attrs, _ in points
        if name == "tokens.used"
    }
    assert ("agent-simple", "input") in tokens_used
    assert ("agent-simple", "output") in tokens_used

    steps_executed = [attrs for name, attrs, _ in points if name == "steps.executed"]
    assert all(attrs["agent_id"] == "agent-simple" for attrs in steps_executed)
    assert all("step_type" in attrs and "outcome" in attrs for attrs in steps_executed)

    assert run.status.value == "completed"


async def test_failed_run_records_failed_status_metric_and_root_span_error(
    instrumented: Instrumented,
) -> None:
    # Seed 0 / agent-flaky / {"prompt": "task"} is known (M3's determinism
    # tests) to exhaust retries into a non-retryable failure.
    run = await run_to_completion(instrumented, "agent-flaky", seed=0, input={"prompt": "task"})
    assert run.status.value == "failed"

    roots = _spans_by_name(instrumented.span_exporter, "run")
    assert len(roots) == 1
    assert roots[0].status.status_code is StatusCode.ERROR

    points = _metric_data_points(instrumented.metric_reader)
    runs_completed = {
        (attrs["agent_id"], attrs["status"])
        for name, attrs, _ in points
        if name == "runs.completed"
    }
    assert ("agent-flaky", "failed") in runs_completed
