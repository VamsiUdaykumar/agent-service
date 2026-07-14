import re

from app.telemetry.ids import generate_trace_id

_TRACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def test_generate_trace_id_format() -> None:
    trace_id = generate_trace_id()
    assert _TRACE_ID_RE.fullmatch(trace_id)
    assert trace_id != "0" * 32


def test_generate_trace_id_unique() -> None:
    ids = {generate_trace_id() for _ in range(1000)}
    assert len(ids) == 1000
