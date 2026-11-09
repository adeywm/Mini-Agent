"""OpenTelemetry tracing + metrics and structured JSON logs.

``setup()`` exports via OTLP/HTTP when ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set (e.g. the
CloudWatch / ADOT collector or AgentCore Observability); otherwise instrumentation is a
no-op. Spans and metrics carry task ids, operations and outcomes, never parameters or
credentials. Every span records the task id so traces correlate with the audit log and
with CloudTrail via the AWS request id attribute.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import metrics, trace

_tracer = trace.get_tracer("infra_agent")
_meter = metrics.get_meter("infra_agent")

executions = _meter.create_counter(
    "infra_agent.executions", description="Broker execution attempts by provider and status"
)
policy_decisions = _meter.create_counter(
    "infra_agent.policy_decisions", description="Policy decisions by decision"
)
tasks_finished = _meter.create_counter(
    "infra_agent.tasks_finished", description="Tasks reaching a final status"
)


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[Any]:
    with _tracer.start_as_current_span(name) as s:
        for key, value in attributes.items():
            if value is not None:
                s.set_attribute(f"infra_agent.{key}", str(value))
        yield s


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ctx = trace.get_current_span().get_span_context()
        payload = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if ctx.is_valid:
            payload["trace_id"] = format(ctx.trace_id, "032x")
            payload["span_id"] = format(ctx.span_id, "016x")
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def setup(service_name: str = "infra-agent", level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)

    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    metrics.set_meter_provider(
        MeterProvider(
            resource=resource, metric_readers=[PeriodicExportingMetricReader(OTLPMetricExporter())]
        )
    )
