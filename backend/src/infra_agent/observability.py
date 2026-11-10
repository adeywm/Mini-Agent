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


class StepLogger:
    """LangChain callback: logs every agent step (LLM call, tool call) as JSON + audit.

    Records tool name, duration, input/output sizes and a short redacted output preview;
    full tool inputs/outputs are not logged (they may be large or sensitive).
    """

    def __init__(self, task_id: str, store: Any = None, preview_chars: int = 200) -> None:
        import time

        from langchain_core.callbacks import BaseCallbackHandler

        self.task_id = task_id
        self.store = store
        self.preview = preview_chars
        self.steps = 0
        self._starts: dict[Any, tuple[str, float]] = {}
        self._clock = time.monotonic
        self._log = logging.getLogger("infra_agent.steps")
        outer = self

        class _Handler(BaseCallbackHandler):
            def on_tool_start(
                self, serialized: dict[str, Any], input_str: str, *, run_id: Any, **_: Any
            ) -> None:
                outer._start(run_id, str((serialized or {}).get("name", "tool")), input_str)

            def on_tool_end(self, output: Any, *, run_id: Any, **_: Any) -> None:
                outer._end(run_id, "ok", output)

            def on_tool_error(self, error: BaseException, *, run_id: Any, **_: Any) -> None:
                outer._end(run_id, "error", error)

            def on_llm_end(self, response: Any, *, run_id: Any, **_: Any) -> None:
                outer._llm(response)

        self.handler = _Handler()

    def _emit(self, action: str, outcome: str, detail: str) -> None:
        from infra_agent.security.redaction import redact_text

        detail = redact_text(detail)
        self._log.info(
            json.dumps(
                {
                    "task_id": self.task_id,
                    "step": self.steps,
                    "action": action,
                    "outcome": outcome,
                    "detail": detail,
                }
            )
        )
        if self.store is not None:
            from infra_agent.models.domain import AuditEvent

            self.store.append_audit(
                [
                    AuditEvent(
                        actor="planner",
                        action=action,
                        target="agent",
                        correlation_id=self.task_id,
                        outcome="error" if outcome == "error" else "info",
                        detail=detail[:500],
                    )
                ]
            )

    def _start(self, run_id: Any, name: str, input_str: str) -> None:
        self.steps += 1
        self._starts[run_id] = (name, self._clock())
        self._emit(f"tool:{name}", "start", f"input {len(input_str)} chars")

    def _end(self, run_id: Any, outcome: str, output: Any) -> None:
        name, t0 = self._starts.pop(run_id, ("tool", self._clock()))
        text = str(getattr(output, "content", output))
        self._emit(
            f"tool:{name}",
            outcome,
            f"{(self._clock() - t0) * 1000:.0f} ms, {len(text)} chars: {text[: self.preview]}",
        )

    def _llm(self, response: Any) -> None:
        self.steps += 1
        usage: dict[str, Any] = {}
        for gens in getattr(response, "generations", []) or []:
            for g in gens:
                meta = getattr(getattr(g, "message", None), "usage_metadata", None) or {}
                for k in ("input_tokens", "output_tokens"):
                    usage[k] = usage.get(k, 0) + int(meta.get(k, 0))
        self._emit(
            "llm",
            "ok",
            f"tokens in={usage.get('input_tokens', 0)} out={usage.get('output_tokens', 0)}",
        )
