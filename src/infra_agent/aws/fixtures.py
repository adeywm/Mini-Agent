"""Recorded-response AWS session for tests and offline demos. Works for any service.

Fixture format (JSON)::

    {
      "<botocore service>": {
        "<Operation>": <response dict>,
        "<Operation>": {"__key__": "<param name>", "values": {"<param value>": <response>},
                        "default": <response>}
      }
    }

Every call passes through ``read_guard.check_operation`` exactly like a live session, so
non-READ operations fail closed in tests too.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from botocore import xform_name

from infra_agent.aws import catalog
from infra_agent.security import read_guard


class FixtureClient:
    def __init__(self, service: str, responses: dict[str, Any]) -> None:
        self._service = service
        self._responses = responses
        self._ops = {xform_name(op): op for op in catalog._service_model(service).operation_names}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def get_paginator(self, method: str) -> Any:
        client = self

        class _Paginator:
            def paginate(self, **params: Any) -> Any:
                yield getattr(client, method)(**params)

        return _Paginator()

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        if name not in self._ops:
            raise AttributeError(f"{self._service} has no operation {name}")
        op = self._ops[name]

        def call(**params: Any) -> Any:
            read_guard.check_operation(self._service, op)
            self.calls.append((op, params))
            if op not in self._responses:
                raise LookupError(f"no fixture for {self._service}:{op}")
            resp = self._responses[op]
            if isinstance(resp, dict) and "__key__" in resp:
                key = str(params.get(resp["__key__"]))
                return resp["values"].get(key, resp.get("default", {}))
            return resp

        return call


class FixtureSession:
    def __init__(self, data: dict[str, dict[str, Any]]) -> None:
        self._data = data
        self.clients: dict[str, FixtureClient] = {}

    @classmethod
    def from_file(cls, path: Path) -> FixtureSession:
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def client(self, service_name: str, /, **_: Any) -> FixtureClient:
        if service_name not in self.clients:
            self.clients[service_name] = FixtureClient(
                service_name, self._data.get(service_name, {})
            )
        return self.clients[service_name]

    def all_calls(self) -> list[tuple[str, str, dict[str, Any]]]:
        return [(svc, op, p) for svc, c in self.clients.items() for op, p in c.calls]
