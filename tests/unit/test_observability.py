from __future__ import annotations

import json
import logging

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from infra_agent.config import AppSettings
from infra_agent.execution.aws_broker import AwsExecutionBroker
from infra_agent.models.domain import ChangePlan
from infra_agent.observability import JsonFormatter, span
from infra_agent.persistence.store import InMemoryStore
from tests.integration.test_aws_broker import _config, _op

EXPORTER = InMemorySpanExporter()
_provider = TracerProvider()
_provider.add_span_processor(SimpleSpanProcessor(EXPORTER))
trace.set_tracer_provider(_provider)


def test_broker_emits_span_without_parameters() -> None:
    EXPORTER.clear()
    op = _op()
    broker = AwsExecutionBroker(_config(), AppSettings(), InMemoryStore(), lambda a: None)
    broker.execute(ChangePlan(id="p", version=1, task_id="task-9", operations=(op,)), op)
    [s] = [s for s in EXPORTER.get_finished_spans() if s.name == "broker.execute"]
    attrs = dict(s.attributes or {})
    assert attrs["infra_agent.task_id"] == "task-9"
    assert attrs["infra_agent.status"] == "BLOCKED"
    assert "dev-cluster" not in json.dumps(attrs)  # parameters are never exported


def test_json_logs_carry_trace_ids() -> None:
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "hello", None, None)
    with span("unit"):
        line = json.loads(JsonFormatter().format(record))
    assert line["msg"] == "hello" and len(line["trace_id"]) == 32
