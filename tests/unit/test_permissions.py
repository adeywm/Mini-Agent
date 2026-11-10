from __future__ import annotations

import pytest

from infra_agent.aws.classifier import classify
from infra_agent.core.operations import AccessClass
from infra_agent.core.permissions import PermissionLevel as L
from infra_agent.core.permissions import has_permission


@pytest.mark.parametrize(
    ("service", "op", "expected"),
    [
        ("ec2", "TerminateInstances", AccessClass.DELETE),
        ("rds", "DeleteDBInstance", AccessClass.DELETE),
        ("ecs", "DeregisterTaskDefinition", AccessClass.DELETE),
        ("ec2", "RunInstances", AccessClass.WRITE),
        ("ec2", "CreateVpc", AccessClass.WRITE),
    ],
)
def test_delete_vs_write(service: str, op: str, expected: AccessClass) -> None:
    assert classify(service, op) is expected


@pytest.mark.parametrize(
    ("granted", "access", "allowed"),
    [
        (L.READ, AccessClass.READ, True),
        (L.READ, AccessClass.WRITE, False),
        (L.WRITE, AccessClass.WRITE, True),
        (L.WRITE, AccessClass.DELETE, False),
        (L.DELETE, AccessClass.DELETE, True),
        (L.DELETE, AccessClass.SENSITIVE_READ, False),
        (L.DELETE, AccessClass.UNKNOWN, False),
    ],
)
def test_levels(granted: L, access: AccessClass, allowed: bool) -> None:
    assert has_permission(granted, access) is allowed
