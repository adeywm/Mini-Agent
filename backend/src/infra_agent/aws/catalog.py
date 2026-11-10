"""Dynamic AWS API discovery from the botocore service models shipped with the SDK.

No service is hard-coded: anything botocore knows (400+ services) can be discovered,
validated and classified. New services arrive by upgrading botocore.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import botocore.session
from botocore.exceptions import UnknownServiceError
from botocore.validate import ParamValidator

from infra_agent.aws.classifier import classify
from infra_agent.core.operations import AccessClass, OperationRef, OperationSpec


class UnknownOperation(LookupError):
    pass


class InvalidParameters(ValueError):
    pass


@lru_cache(maxsize=1)
def _session() -> Any:
    return botocore.session.get_session()


@lru_cache(maxsize=512)
def _service_model(service: str) -> Any:
    try:
        return _session().get_service_model(service)
    except UnknownServiceError as exc:
        raise UnknownOperation(f"unknown AWS service {service!r}") from exc


def list_services() -> list[str]:
    return sorted(_session().get_available_services())


def list_operations(service: str, access: AccessClass | None = None) -> list[OperationSpec]:
    model = _service_model(service)
    specs = [describe(service, name) for name in model.operation_names]
    return [s for s in specs if access is None or s.access is access]


def describe(service: str, operation: str) -> OperationSpec:
    model = _service_model(service)
    if operation not in model.operation_names:
        raise UnknownOperation(f"unknown AWS operation {service}:{operation}")
    op = model.operation_model(operation)
    shape = op.input_shape
    params = tuple(shape.members) if shape is not None else ()
    required = tuple(shape.required_members) if shape is not None else ()
    doc = (op.documentation or "").replace("<p>", "").replace("</p>", " ")
    return OperationSpec(
        ref=OperationRef(provider="aws", service=service, operation=operation),
        access=classify(service, operation),
        documentation=doc[:500],
        required_params=required,
        params=params,
        paginated=_can_paginate(service, operation),
    )


def _can_paginate(service: str, operation: str) -> bool:
    try:
        return operation in _session().get_paginator_model(service)._paginator_config
    except Exception:
        return False


def validate_params(service: str, operation: str, params: dict[str, Any]) -> None:
    op = _service_model(service).operation_model(operation)
    if op.input_shape is None:
        if params:
            raise InvalidParameters(f"{service}:{operation} takes no parameters")
        return
    report = ParamValidator().validate(params, op.input_shape)
    if report.has_errors():
        raise InvalidParameters(report.generate_report())


def method_name(service: str, operation: str) -> str:
    """boto3 client method for an operation (uses botocore's own mapping)."""
    from botocore import xform_name

    _service_model(service)  # validate service
    return str(xform_name(operation))
