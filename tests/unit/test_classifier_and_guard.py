from __future__ import annotations

import boto3
import pytest

from infra_agent.aws import catalog
from infra_agent.aws.classifier import SENSITIVE_OPERATIONS, classify
from infra_agent.core.operations import AccessClass
from infra_agent.security import read_guard


@pytest.mark.parametrize(
    ("service", "operation", "expected"),
    [
        ("ecs", "DescribeServices", AccessClass.READ),
        ("ec2", "DescribeInstances", AccessClass.READ),
        ("s3", "ListBuckets", AccessClass.READ),
        ("logs", "FilterLogEvents", AccessClass.READ),
        ("iam", "SimulatePrincipalPolicy", AccessClass.READ),
        ("ec2", "TerminateInstances", AccessClass.DELETE),
        ("ecs", "UpdateService", AccessClass.WRITE),
        ("iam", "CreatePolicyVersion", AccessClass.WRITE),
        ("lambda", "Invoke", AccessClass.WRITE),
        ("sqs", "ReceiveMessage", AccessClass.WRITE),
        ("cloudtrail", "StopLogging", AccessClass.WRITE),
        ("secretsmanager", "GetSecretValue", AccessClass.SENSITIVE_READ),
        ("ssm", "GetParameter", AccessClass.SENSITIVE_READ),
        ("s3", "GetObject", AccessClass.SENSITIVE_READ),
        ("ecr", "GetAuthorizationToken", AccessClass.SENSITIVE_READ),
        ("sts", "GetSessionToken", AccessClass.SENSITIVE_READ),
        ("ec2", "GetPasswordData", AccessClass.SENSITIVE_READ),
        ("rds", "DescribeDBInstances", AccessClass.READ),
    ],
)
def test_classification(service: str, operation: str, expected: AccessClass) -> None:
    assert classify(service, operation) is expected


def test_sensitive_list_refers_to_real_operations() -> None:
    for service, op in SENSITIVE_OPERATIONS:
        assert catalog.describe(service, op).access is AccessClass.SENSITIVE_READ


def test_every_botocore_operation_is_classified_without_unknowns() -> None:
    unknown = [
        (svc, op)
        for svc in catalog.list_services()
        for op in catalog._service_model(svc).operation_names
        if classify(svc, op) is AccessClass.UNKNOWN
    ]
    assert unknown == []


@pytest.mark.parametrize(
    ("service", "operation"),
    [("ec2", "TerminateInstances"), ("secretsmanager", "GetSecretValue"), ("sts", "AssumeRole")],
)
def test_guard_rejects(service: str, operation: str) -> None:
    with pytest.raises(read_guard.ReadOnlyViolation):
        read_guard.check_operation(service, operation)


def test_guard_blocks_real_boto3_client_before_network() -> None:
    """A real boto3 client on a guarded session cannot send a mutating request.

    The endpoint is unroutable: if the guard did not fire, the call would fail with a
    connection error instead of ReadOnlyViolation.
    """
    session = read_guard.install(boto3.Session(region_name="ap-south-1"))
    ec2 = session.client("ec2", endpoint_url="http://127.0.0.1:9")
    with pytest.raises(read_guard.ReadOnlyViolation):
        ec2.terminate_instances(InstanceIds=["i-0123456789abcdef0"])
    sm = session.client("secretsmanager", endpoint_url="http://127.0.0.1:9")
    with pytest.raises(read_guard.ReadOnlyViolation):
        sm.get_secret_value(SecretId="x")


def test_guarded_session_keeps_credential_resolution_unguarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import botocore.credentials

    creds = botocore.credentials.Credentials("AKIDEXAMPLE", "secret")

    class _Source:
        region_name = "ap-south-1"

        def __init__(self, **_: object) -> None: ...

        def get_credentials(self) -> object:
            return creds

    real_session = boto3.Session
    monkeypatch.setattr(
        boto3, "Session", lambda **kw: _Source() if "profile_name" in kw else real_session(**kw)
    )
    session = read_guard.guarded_session("infra-agent-read")
    assert session.get_credentials() is creds
    ec2 = session.client("ec2", endpoint_url="http://127.0.0.1:9")
    with pytest.raises(read_guard.ReadOnlyViolation):
        ec2.terminate_instances(InstanceIds=["i-0123456789abcdef0"])
