from __future__ import annotations

import copy
from typing import Any

import pytest

from infra_agent.config import AgentConfig
from infra_agent.models.domain import Decision, PlannedOperation
from infra_agent.policy.engine import PolicyEngine
from tests.conftest import config_dict


def _cfg(level: int = 3, rules: list[dict[str, Any]] | None = None, **acct: Any) -> AgentConfig:
    data = copy.deepcopy(config_dict())
    data["accounts"][0].update({"permission_level": level, **acct})
    data["policy"] = {"rules": rules or []}
    data["budgets"] = {"max_estimated_new_monthly_usd": 20}
    return AgentConfig.model_validate(data)


def _op(
    service: str, operation: str, params: dict[str, Any] | None = None, **over: Any
) -> PlannedOperation:
    base: dict[str, Any] = {
        "operation_id": "op-1",
        "provider": "aws",
        "account_alias": "dev",
        "environment": "development",
        "service": service,
        "operation": operation,
        "region": "ap-south-1",
        "resource_scope": ("x",),
        "parameters": params or {},
        "expected_outcome": "-",
        "verification": "-",
        "idempotency_key": "k",
    }
    base.update(over)
    return PlannedOperation.model_validate(base)


def _decide(cfg: AgentConfig, op: PlannedOperation) -> Decision:
    return PolicyEngine(cfg).evaluate(op, "sha256:p").decision


def test_defaults_by_access_class() -> None:
    cfg = _cfg()
    assert _decide(cfg, _op("ec2", "DescribeInstances")) is Decision.ALLOW_AUTO
    assert _decide(cfg, _op("ecs", "UpdateService")) is Decision.REQUIRE_APPROVAL
    d = PolicyEngine(cfg).evaluate(_op("ec2", "TerminateInstances"), "sha256:p")
    assert d.decision is Decision.REQUIRE_APPROVAL and d.required_approval_level == "high"


@pytest.mark.parametrize(
    ("level", "op", "expected"),
    [
        (1, ("ecs", "UpdateService"), Decision.DENY),
        (1, ("ec2", "TerminateInstances"), Decision.DENY),
        (2, ("ec2", "TerminateInstances"), Decision.DENY),
        (2, ("ecs", "UpdateService"), Decision.ALLOW_AUTO),
        (1, ("ec2", "DescribeInstances"), Decision.ALLOW_AUTO),
    ],
)
def test_permission_level_gate_before_approval(
    level: int, op: tuple[str, str], expected: Decision
) -> None:
    allow_all = [{"id": "everything", "effect": "ALLOW_AUTO"}]
    assert _decide(_cfg(level, allow_all), _op(*op)) is expected


@pytest.mark.parametrize(
    ("service", "operation"),
    [
        ("cloudtrail", "StopLogging"),
        ("iam", "AttachRolePolicy"),
        ("iam", "PutUserPolicy"),
        ("iam", "CreateAccessKey"),
        ("sts", "AssumeRole"),
        ("kms", "ScheduleKeyDeletion"),
        ("rds", "DeleteDBSnapshot"),
        ("organizations", "LeaveOrganization"),
    ],
)
def test_prohibited_even_with_allow_rule_and_level_3(service: str, operation: str) -> None:
    cfg = _cfg(3, [{"id": "yolo", "effect": "ALLOW_AUTO"}])
    assert _decide(cfg, _op(service, operation)) is Decision.DENY


def test_scope_checks() -> None:
    cfg = _cfg()
    assert _decide(cfg, _op("ec2", "DescribeInstances", region="us-east-1")) is Decision.DENY
    assert _decide(cfg, _op("ec2", "DescribeInstances", account_alias="prod")) is Decision.DENY
    assert _decide(cfg, _op("ec2", "DescribeInstances", environment="production")) is Decision.DENY


def test_sensitive_read_needs_explicit_grant() -> None:
    op = _op("secretsmanager", "GetSecretValue", {"SecretId": "x"})
    assert _decide(_cfg(3, [{"id": "all", "effect": "ALLOW_AUTO"}]), op) is Decision.DENY
    grant = [
        {
            "id": "s",
            "effect": "ALLOW_AUTO",
            "service": "secretsmanager",
            "operation": "GetSecretValue",
        }
    ]
    assert _decide(_cfg(3, grant), op) is Decision.REQUIRE_APPROVAL


def test_auto_rule_bounded_by_cost_and_public_exposure() -> None:
    rule = [{"id": "dev-ec2", "effect": "ALLOW_AUTO", "service": "ec2"}]
    cfg = _cfg(2, rule)
    run = {"ImageId": "ami-1", "MinCount": 1, "MaxCount": 1}
    assert _decide(cfg, _op("ec2", "RunInstances", run)) is Decision.REQUIRE_APPROVAL
    assert (
        _decide(cfg, _op("ec2", "RunInstances", run, estimated_cost_change_usd_monthly=8.0))
        is Decision.ALLOW_AUTO
    )
    assert (
        _decide(cfg, _op("ec2", "RunInstances", run, estimated_cost_change_usd_monthly=500.0))
        is Decision.REQUIRE_APPROVAL
    )
    open_world = {"GroupId": "sg-1", "IpPermissions": [{"IpRanges": [{"CidrIp": "0.0.0.0/0"}]}]}
    d = PolicyEngine(cfg).evaluate(_op("ec2", "AuthorizeSecurityGroupIngress", open_world), "p")
    assert d.decision is Decision.REQUIRE_APPROVAL and d.required_approval_level == "high"


def test_deny_rule_wins() -> None:
    rules = [
        {"id": "allow", "effect": "ALLOW_AUTO"},
        {"id": "no-ecs", "effect": "DENY", "service": "ecs"},
    ]
    assert _decide(_cfg(3, rules), _op("ecs", "UpdateService")) is Decision.DENY


def _gh(method: str, path: str, repo: str = "example-org/backend") -> PlannedOperation:
    return _op(method, path, {"repository": repo}, provider="github")


def test_github_operations() -> None:
    cfg = _cfg(3)
    assert (
        _decide(cfg, _gh("GET", "/repos/example-org/backend/actions/runs")) is Decision.ALLOW_AUTO
    )
    assert (
        _decide(cfg, _gh("POST", "/repos/example-org/backend/pulls")) is Decision.REQUIRE_APPROVAL
    )
    # path/repository mismatch, unlisted repo, protected admin ops, secrets
    assert _decide(cfg, _gh("GET", "/repos/other/repo/contents/x")) is Decision.DENY
    assert _decide(cfg, _gh("GET", "/repos/other/repo/pulls", "other/repo")) is Decision.DENY
    assert _decide(cfg, _gh("DELETE", "/repos/example-org/backend")) is Decision.DENY
    assert (
        _decide(cfg, _gh("PUT", "/repos/example-org/backend/branches/main/protection"))
        is Decision.DENY
    )
    assert _decide(cfg, _gh("GET", "/repos/example-org/backend/actions/secrets")) is Decision.DENY
    assert _decide(cfg, _gh("GET", "/repos/example-org/backend/../x")) is Decision.DENY
