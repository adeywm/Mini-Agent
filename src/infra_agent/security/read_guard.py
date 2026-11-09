"""In-process guard: only READ-classified AWS operations may leave this process.

Generic across all AWS services: classification comes from ``aws.classifier``, not a
per-service allowlist. This is defence in depth. The real boundary is the read-only IAM
identity, which ``security.credentials`` verifies before live reads are enabled. Even a
misconfigured, over-privileged profile cannot be used by this process to mutate anything or
read secret material: the guard is attached to the botocore session and fails closed.
"""

from __future__ import annotations

from typing import Any

from infra_agent.aws.classifier import classify
from infra_agent.core.operations import AccessClass


class ReadOnlyViolation(PermissionError):
    """An AWS operation that is not a plain READ was attempted."""


def check_operation(service: str, operation: str) -> None:
    access = classify(service, operation)
    if access is not AccessClass.READ:
        raise ReadOnlyViolation(
            f"AWS operation {service}:{operation} is {access}; only READ is permitted"
        )


def _before_call(model: Any, **_: Any) -> None:
    check_operation(model.service_model.service_name, model.name)


def install(session: Any) -> Any:
    """Attach the guard to a ``boto3.Session`` (or botocore session). Returns the session."""
    botocore_session = getattr(session, "_session", session)
    botocore_session.register("before-call", _before_call, unique_id="infra-agent-read-guard")
    return session
