from __future__ import annotations

from typing import Any

import pytest

from infra_agent.aws.catalog import InvalidParameters, UnknownOperation
from infra_agent.aws.fixtures import FixtureSession
from infra_agent.aws.reader import AwsReader
from infra_agent.config import AgentConfig
from infra_agent.core.operations import (
    OperationDenied,
    OperationRef,
    OperationRequest,
    ResourceScope,
)


def _req(
    service: str, op: str, params: dict[str, Any] | None = None, **scope: Any
) -> OperationRequest:
    return OperationRequest(
        ref=OperationRef(provider="aws", service=service, operation=op),
        scope=ResourceScope(**({"account_alias": "dev", "region": "ap-south-1"} | scope)),
        params=params or {},
    )


def test_generic_read_any_service(cfg: AgentConfig) -> None:
    session = FixtureSession(
        {
            "rds": {
                "DescribeDBInstances": {
                    "DBInstances": [{"DBInstanceIdentifier": "db1", "MasterUserPassword": "x"}]
                }
            }
        }
    )
    reader = AwsReader(session, cfg.account("dev"))
    result = reader.call(_req("rds", "DescribeDBInstances"))
    assert result.data["DBInstances"][0]["DBInstanceIdentifier"] == "db1"
    assert "x" not in str(result.data["DBInstances"][0].get("MasterUserPassword"))
    assert reader.evidence[0].id == result.evidence_id
    assert session.all_calls() == [("rds", "DescribeDBInstances", {})]


@pytest.mark.parametrize(
    ("service", "op", "params"),
    [
        ("ec2", "TerminateInstances", {"InstanceIds": ["i-1"]}),
        ("ecs", "DeleteService", {"service": "api"}),
        ("iam", "CreatePolicyVersion", {"PolicyArn": "arn", "PolicyDocument": "{}"}),
        ("secretsmanager", "GetSecretValue", {"SecretId": "x"}),
        ("s3", "GetObject", {"Bucket": "b", "Key": "k"}),
    ],
)
def test_non_read_refused_before_any_call(
    cfg: AgentConfig, service: str, op: str, params: dict[str, Any]
) -> None:
    session = FixtureSession({})
    reader = AwsReader(session, cfg.account("dev"))
    with pytest.raises(OperationDenied):
        reader.call(_req(service, op, params))
    assert session.all_calls() == []


def test_scope_enforced(cfg: AgentConfig) -> None:
    reader = AwsReader(FixtureSession({}), cfg.account("dev"))
    with pytest.raises(OperationDenied, match="region"):
        reader.call(_req("ec2", "DescribeInstances", region="us-east-1"))
    with pytest.raises(OperationDenied, match="account"):
        reader.call(_req("ec2", "DescribeInstances", account_alias="prod"))


def test_unknown_and_invalid_rejected(cfg: AgentConfig) -> None:
    reader = AwsReader(FixtureSession({}), cfg.account("dev"))
    with pytest.raises(UnknownOperation):
        reader.call(_req("notaservice", "DescribeThings"))
    with pytest.raises(UnknownOperation):
        reader.call(_req("ec2", "DescribeUnicorns"))
    with pytest.raises(InvalidParameters):
        reader.call(_req("ecs", "DescribeServices", {"cluster": 42}))
    with pytest.raises(InvalidParameters):
        reader.call(_req("ecs", "DescribeServices", {"services": ["a"], "bogus": 1}))


def test_pagination_bounded(cfg: AgentConfig) -> None:
    session = FixtureSession({"ec2": {"DescribeInstances": {"Reservations": []}}})
    reader = AwsReader(session, cfg.account("dev"))
    req = OperationRequest(
        ref=OperationRef(provider="aws", service="ec2", operation="DescribeInstances"),
        scope=ResourceScope(account_alias="dev", region="ap-south-1"),
        max_pages=3,
    )
    result = reader.call(req)
    assert result.pages == 1
