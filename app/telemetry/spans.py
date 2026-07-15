"""Span tree helpers: one root `run` span per run, one child span per step
(sub-agent steps nest their own children exactly one level deeper by simply
calling `start_step_span` again with the sub-agent's span as parent), and
one child span per retry attempt — so retry latency/backoff shows as bars
and gaps in the waterfall (PRD §3.4).

GenAI semantic-convention attributes are still "incubating" in the OTel spec
as of this SDK version, so the keys are hardcoded here rather than imported
from the private `opentelemetry.semconv._incubating` module path, which
isn't a stable import surface.
"""

from __future__ import annotations

from opentelemetry.sdk.trace.id_generator import RandomIdGenerator
from opentelemetry.trace import (
    NonRecordingSpan,
    Span,
    SpanContext,
    SpanKind,
    Status,
    StatusCode,
    TraceFlags,
    Tracer,
    set_span_in_context,
)

from app.domain.errors import RunError
from app.domain.status import StepType

GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"

STACKAI_RUN_ID = "stackai.run.id"
STACKAI_AGENT_ID = "stackai.agent_id"
STACKAI_STEP_ID = "stackai.step_id"
STACKAI_STEP_TYPE = "stackai.step_type"
STACKAI_PARENT_STEP_ID = "stackai.parent_step_id"
STACKAI_ATTEMPT = "stackai.attempt"
STACKAI_COST_USD = "stackai.cost_usd"
STACKAI_ERROR_CODE = "stackai.error.code"
STACKAI_ERROR_RETRYABLE = "stackai.error.retryable"

_id_generator = RandomIdGenerator()


def start_run_span(tracer: Tracer, *, run_id: str, agent_id: str, trace_id: str) -> Span:
    """Open the root `run` span, adopting `trace_id` (already persisted at
    run creation, M4.T3.1) as the OTel trace ID instead of letting the SDK
    mint a fresh one — done by starting the span inside an explicit
    `NonRecordingSpan` context carrying that trace ID as a synthetic remote
    parent. This is what keeps the envelope's `trace_id` and the actual
    OTel trace ID in agreement (amendment 2).
    """
    fake_parent = SpanContext(
        trace_id=int(trace_id, 16),
        span_id=_id_generator.generate_span_id(),
        is_remote=True,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )
    context = set_span_in_context(NonRecordingSpan(fake_parent))
    span = tracer.start_span("run", context=context, kind=SpanKind.INTERNAL)
    span.set_attribute(STACKAI_RUN_ID, run_id)
    span.set_attribute(STACKAI_AGENT_ID, agent_id)
    return span


def start_step_span(
    tracer: Tracer,
    *,
    parent: Span,
    step_id: str,
    step_type: StepType,
    parent_step_id: str | None,
) -> Span:
    """Standard per-step child span. Sub-agent nesting (M7.T2.4) needs no
    separate helper: a sub-agent step's children are just further
    `start_step_span` calls with the sub-agent's own span passed as
    `parent`.
    """
    span = tracer.start_span(
        step_type.value, context=set_span_in_context(parent), kind=SpanKind.INTERNAL
    )
    span.set_attribute(STACKAI_STEP_ID, step_id)
    span.set_attribute(STACKAI_STEP_TYPE, step_type.value)
    if parent_step_id is not None:
        span.set_attribute(STACKAI_PARENT_STEP_ID, parent_step_id)
    return span


def start_attempt_span(tracer: Tracer, *, parent: Span, attempt: int) -> Span:
    """One child span per retry attempt, under its step span — this is what
    makes retry latency and backoff gaps visible as distinct bars in the
    waterfall (PRD §3.4).
    """
    span = tracer.start_span(
        f"attempt-{attempt}", context=set_span_in_context(parent), kind=SpanKind.INTERNAL
    )
    span.set_attribute(STACKAI_ATTEMPT, attempt)
    return span


def set_genai_attributes(
    span: Span, *, model_name: str | None, tokens_in: int | None, tokens_out: int | None
) -> None:
    if model_name is not None:
        span.set_attribute(GEN_AI_REQUEST_MODEL, model_name)
    if tokens_in is not None:
        span.set_attribute(GEN_AI_USAGE_INPUT_TOKENS, tokens_in)
    if tokens_out is not None:
        span.set_attribute(GEN_AI_USAGE_OUTPUT_TOKENS, tokens_out)


def set_cost_attribute(span: Span, cost_usd: float | None) -> None:
    if cost_usd is not None:
        span.set_attribute(STACKAI_COST_USD, cost_usd)


def record_error(span: Span, error: RunError) -> None:
    """Set span status to ERROR and record the structured `RunError` (M1.T2)
    as span attributes + an event, on step/attempt failure (M7.T3.3).
    """
    span.set_status(Status(StatusCode.ERROR, error.message))
    span.set_attribute(STACKAI_ERROR_CODE, error.code.value)
    span.set_attribute(STACKAI_ERROR_RETRYABLE, error.retryable)
    span.add_event(
        "error",
        {
            STACKAI_ERROR_CODE: error.code.value,
            "error.message": error.message,
            STACKAI_ERROR_RETRYABLE: error.retryable,
        },
    )
