"""OTel SDK setup: `TracerProvider` and `MeterProvider`, OTLP exporters
pointed at `Settings.otel_exporter_otlp_endpoint`, resource attributes.

Both `configure_tracing`/`configure_metrics` accept an optional exporter/
reader override — real OTLP in prod, `InMemorySpanExporter`/
`InMemoryMetricReader` in tests, per PRD §6 ("we test our instrumentation,
not the vendor").
"""

from __future__ import annotations

from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import MetricReader, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter

from app.config import Settings


def _resource(settings: Settings) -> Resource:
    return Resource.create(
        {
            "service.name": settings.otel_service_name,
            "service.version": settings.otel_service_version,
        }
    )


def configure_tracing(
    settings: Settings, span_exporter: SpanExporter | None = None
) -> TracerProvider:
    provider = TracerProvider(resource=_resource(settings))
    exporter = span_exporter or OTLPSpanExporter(
        endpoint=f"{settings.otel_exporter_otlp_endpoint}/v1/traces"
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    return provider


def configure_metrics(
    settings: Settings, metric_reader: MetricReader | None = None
) -> MeterProvider:
    reader = metric_reader or PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=f"{settings.otel_exporter_otlp_endpoint}/v1/metrics")
    )
    return MeterProvider(resource=_resource(settings), metric_readers=[reader])
