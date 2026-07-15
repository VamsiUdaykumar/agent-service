"""Fixtures for driving a real `RunService` end to end with in-memory OTel
exporters — PRD §6: "we test our instrumentation, not the vendor."
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.persistence.models import RunRecord
from app.persistence.sqlite_repository import SqliteRepository
from app.services.run_service import RunService

FAST = 50_000.0


@dataclass
class Instrumented:
    service: RunService
    repository: SqliteRepository
    span_exporter: InMemorySpanExporter
    metric_reader: InMemoryMetricReader


@pytest.fixture
async def instrumented(tmp_path: Path) -> AsyncIterator[Instrumented]:
    repository = await SqliteRepository.connect(str(tmp_path / "test.db"))

    span_exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    # SimpleSpanProcessor exports synchronously on span.end() — no batching
    # delay to race against when asserting on finished spans right after a
    # run completes.
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    tracer = tracer_provider.get_tracer("test")

    metric_reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[metric_reader])
    meter = meter_provider.get_meter("test")

    service = RunService(repository, sim_speed=FAST, tracer=tracer, meter=meter)
    try:
        yield Instrumented(service, repository, span_exporter, metric_reader)
    finally:
        await repository.close()


async def run_to_completion(
    instrumented: Instrumented,
    agent_id: str,
    seed: int,
    input: dict | None = None,
    metadata: dict[str, str] | None = None,
) -> RunRecord:
    record = await instrumented.service.create_run(
        agent_id=agent_id, input=input or {"prompt": "x"}, seed=seed, metadata=metadata
    )
    for _ in range(2000):
        current = await instrumented.repository.get_run(record.id)
        assert current is not None
        if current.status.value in ("completed", "failed", "cancelled"):
            return current
        await asyncio.sleep(0.005)
    raise AssertionError(f"run {record.id} did not reach a terminal state in time")
