"""Generic read-only AWS executor: one code path for every service and operation.

Pipeline for each ``OperationRequest``:
scope check (operator config) -> botocore discovery -> access classification (READ only)
-> parameter shape validation -> guarded boto3 call (optional bounded pagination)
-> redaction -> ``Evidence``.

The model never supplies credentials, roles or accounts; it can only name an operation and
parameters, which are validated here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from infra_agent.aws import catalog
from infra_agent.config import AccountConfig
from infra_agent.core.operations import (
    AccessClass,
    OperationDenied,
    OperationRequest,
    OperationResult,
)
from infra_agent.models.domain import Evidence, sha256_digest
from infra_agent.security import read_guard
from infra_agent.security.redaction import redact

_MAX_RESULT_CHARS = 200_000


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items() if k != "ResponseMetadata"}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if hasattr(value, "read"):  # streaming bodies are never read
        return "<stream omitted>"
    return value


class AwsReader:
    def __init__(self, session: Any, account: AccountConfig) -> None:
        self._session = read_guard.install(session)
        self._account = account
        self.evidence: list[Evidence] = []

    def call(self, request: OperationRequest) -> OperationResult:
        ref, scope = request.ref, request.scope
        if ref.provider != "aws":
            raise OperationDenied("AwsReader only executes AWS operations")
        if scope.account_alias != self._account.alias:
            raise OperationDenied("request targets a different account alias than this reader")
        if scope.region not in self._account.allowed_regions:
            raise OperationDenied(f"region {scope.region!r} is not allowed for this account")

        spec = catalog.describe(ref.service, ref.operation)
        if spec.access is not AccessClass.READ:
            raise OperationDenied(f"{ref} is classified {spec.access}; only READ is executed")
        catalog.validate_params(ref.service, ref.operation, request.params)

        client = self._session.client(ref.service, region_name=scope.region)
        method = catalog.method_name(ref.service, ref.operation)
        pages: list[dict[str, Any]] = []
        truncated = False
        if spec.paginated and request.max_pages > 1:
            paginator = client.get_paginator(method)
            for i, page in enumerate(paginator.paginate(**request.params)):
                if i >= request.max_pages:
                    truncated = True
                    break
                pages.append(page)
        else:
            pages.append(getattr(client, method)(**request.params))

        data: dict[str, Any] = redact(_jsonable(pages[0] if len(pages) == 1 else {"pages": pages}))
        text_len = len(str(data))
        if text_len > _MAX_RESULT_CHARS:
            data = {"truncated": True, "size": text_len, "preview": str(data)[:_MAX_RESULT_CHARS]}
            truncated = True

        ev_id = (
            "ev-aws-"
            + sha256_digest({"ref": str(ref), "region": scope.region, "params": request.params})[
                7:19
            ]
        )
        self.evidence.append(
            Evidence(
                id=ev_id,
                source=f"aws:{ref.service}:{ref.operation}",
                collected_at=datetime.now(UTC),
                summary=f"{ref.service}:{ref.operation} in {scope.region} ({len(pages)} page(s))",
                data=data,
            )
        )
        return OperationResult(
            ref=ref,
            scope=scope,
            evidence_id=ev_id,
            pages=len(pages),
            truncated=truncated,
            data=data,
        )
