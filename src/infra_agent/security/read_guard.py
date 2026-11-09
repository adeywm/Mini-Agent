"""In-process allowlist of AWS API operations the read-only collector may call.

This is defence in depth. The real boundary is the read-only IAM identity, which
``security.credentials`` verifies before live reads are enabled. The guard ensures that even
a misconfigured, over-privileged profile cannot be used by this process to mutate anything:
it is attached to the botocore session and fails closed on every operation not listed here.
"""

from __future__ import annotations

from typing import Any

# (botocore service name, API operation name). Explicit, reviewed list only.
READ_ALLOWLIST: frozenset[tuple[str, str]] = frozenset(
    {
        ("sts", "GetCallerIdentity"),
        ("iam", "GetRole"),
        ("iam", "SimulatePrincipalPolicy"),
        ("ecs", "DescribeClusters"),
        ("ecs", "ListServices"),
        ("ecs", "DescribeServices"),
        ("ecs", "ListTasks"),
        ("ecs", "DescribeTasks"),
        ("ecs", "DescribeTaskDefinition"),
        ("elbv2", "DescribeTargetHealth"),
    }
)

# Reads that can expose secret material; never allowed even if someone adds them above.
SENSITIVE_READS: frozenset[tuple[str, str]] = frozenset(
    {
        ("secretsmanager", "GetSecretValue"),
        ("ssm", "GetParameter"),
        ("ssm", "GetParameters"),
        ("ssm", "GetParametersByPath"),
        ("kms", "Decrypt"),
        ("sts", "AssumeRole"),
        ("sts", "GetSessionToken"),
        ("sts", "GetFederationToken"),
        ("ecr", "GetAuthorizationToken"),
    }
)


class ReadOnlyViolation(PermissionError):
    """An AWS operation outside the read allowlist was attempted."""


def check_operation(service: str, operation: str) -> None:
    key = (service, operation)
    if key in SENSITIVE_READS or key not in READ_ALLOWLIST:
        raise ReadOnlyViolation(f"AWS operation {service}:{operation} is not permitted (read-only)")


def _before_call(model: Any, **_: Any) -> None:
    check_operation(model.service_model.service_name, model.name)


def install(session: Any) -> Any:
    """Attach the guard to a ``boto3.Session`` (or botocore session). Returns the session."""
    botocore_session = getattr(session, "_session", session)
    botocore_session.register("before-call", _before_call, unique_id="infra-agent-read-guard")
    return session
