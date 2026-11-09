"""IAM in the CloudFormation template must cover what the code prohibits, so IAM (the real
boundary) and the policy engine never drift apart."""

from __future__ import annotations

import fnmatch
import json
from pathlib import Path
from typing import Any

from infra_agent.aws.classifier import SENSITIVE_OPERATIONS
from infra_agent.policy.engine import PROHIBITED

TPL = json.loads(
    (Path(__file__).resolve().parents[2] / "infra/cloudformation/infra-agent.json").read_text()
)


def _deny_actions(doc: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for st in doc["Statement"]:
        if st["Effect"] == "Deny":
            acts = st["Action"]
            out += [acts] if isinstance(acts, str) else acts
    return out


def _covered(action: str, denies: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(action, d) or d == action for d in denies)


def test_boundary_denies_every_prohibited_operation() -> None:
    denies = _deny_actions(TPL["Resources"]["ExecutionBoundary"]["Properties"]["PolicyDocument"])
    for service, op, _ in PROHIBITED:
        assert f"{service}:{op}" in denies, (service, op)


def test_read_role_denies_sensitive_reads_and_is_read_only() -> None:
    props = TPL["Resources"]["ReadRole"]["Properties"]
    assert props["ManagedPolicyArns"] == ["arn:aws:iam::aws:policy/ReadOnlyAccess"]
    denies: list[str] = []
    for pol in props["Policies"]:
        denies += _deny_actions(pol["PolicyDocument"])
    for service, op in SENSITIVE_OPERATIONS:
        assert _covered(f"{service}:{op}", denies), (service, op)


def test_execution_role_has_boundary_and_no_wildcard_grant() -> None:
    props = TPL["Resources"]["ExecutionRole"]["Properties"]
    assert props["PermissionsBoundary"] == {"Ref": "ExecutionBoundary"}
    assert "Policies" not in props  # grants come only from operator-chosen managed policies
