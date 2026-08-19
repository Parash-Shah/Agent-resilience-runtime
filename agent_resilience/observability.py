from __future__ import annotations

from typing import Any

from .config import Settings


def configure_otel(app: Any, config: Settings) -> bool:
    """Enable OTLP export when optional dependencies and an endpoint are present."""
    if not config.otel_exporter_otlp_endpoint:
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        return False
    provider = TracerProvider(resource=Resource.create({"service.name": "agent-resilience-runtime"}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=config.otel_exporter_otlp_endpoint)))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)
    return True
