from opentelemetry.sdk.trace.id_generator import RandomIdGenerator
from opentelemetry.trace import format_trace_id

# A dedicated generator, independent of the full OTel SDK setup (Milestone 7's
# TracerProvider). This lets `generate_trace_id` be called at run-creation
# time (M4.T3.1) before telemetry is configured, while still producing IDs
# that are valid to later start a real OTel span with (M7.T2.1).
_id_generator = RandomIdGenerator()


def generate_trace_id() -> str:
    """Return a fresh, W3C-trace-context-compatible trace ID: 32 lowercase hex chars."""
    return format_trace_id(_id_generator.generate_trace_id())
