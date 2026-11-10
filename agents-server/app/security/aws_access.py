"""Deterministic access classification for AWS API operations, and a read-only boto3 guard.

Ported from the earlier implementation (main branch). Name prefixes are a starting point:
unknown names are never treated as READ, and operations that return secret material are
SENSITIVE_READ (never run automatically).
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any


class AccessClass(StrEnum):
    READ = "READ"
    SENSITIVE_READ = "SENSITIVE_READ"
    WRITE = "WRITE"
    DELETE = "DELETE"
    UNKNOWN = "UNKNOWN"


_DELETE_PREFIXES = ("Delete", "Terminate", "Destroy", "Remove", "Purge", "Deregister")
_READ_PREFIXES = ("Describe", "List", "Get", "BatchGet", "Search", "Lookup", "Head", "Filter")

SENSITIVE_OPERATIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("secretsmanager", "GetSecretValue"),
        ("secretsmanager", "BatchGetSecretValue"),
        ("ssm", "GetParameter"),
        ("ssm", "GetParameters"),
        ("ssm", "GetParametersByPath"),
        ("ssm", "GetParameterHistory"),
        ("ec2", "GetPasswordData"),
        ("ec2", "GetConsoleOutput"),
        ("ec2", "GetConsoleScreenshot"),
        ("ec2", "GetLaunchTemplateData"),
        ("lambda", "GetFunction"),
        ("s3", "GetObject"),
        ("s3", "GetObjectTorrent"),
        ("dynamodb", "GetItem"),
        ("dynamodb", "BatchGetItem"),
        ("sts", "GetSessionToken"),
        ("sts", "GetFederationToken"),
        ("sts", "GetWebIdentityToken"),
        ("ecr", "GetAuthorizationToken"),
        ("codeartifact", "GetAuthorizationToken"),
        ("cognito-identity", "GetCredentialsForIdentity"),
        ("cognito-identity", "GetOpenIdToken"),
        ("sso", "GetRoleCredentials"),
        ("iam", "GetCredentialReport"),
        ("kinesis", "GetRecords"),
        ("glue", "GetConnection"),
        ("apigateway", "GetApiKey"),
        ("apigateway", "GetExport"),
    }
)

_SENSITIVE_NAME = re.compile(
    r"(SecretValue|Password|Token|Credential|PrivateKey|AccessKey|AuthorizationData|Decrypt)"
)
_NOT_READ: frozenset[tuple[str, str]] = frozenset(
    {("sqs", "ReceiveMessage"), ("ec2", "GetConsoleScreenshot")}
)
_EXTRA_READ: frozenset[tuple[str, str]] = frozenset(
    {("iam", "SimulatePrincipalPolicy"), ("iam", "SimulateCustomPolicy")}
)


def classify(service: str, operation: str) -> AccessClass:
    key = (service, operation)
    if key in _EXTRA_READ:
        return AccessClass.READ
    if key in SENSITIVE_OPERATIONS:
        return AccessClass.SENSITIVE_READ
    if key in _NOT_READ:
        return AccessClass.WRITE
    if operation.startswith(_READ_PREFIXES):
        if _SENSITIVE_NAME.search(operation):
            return AccessClass.SENSITIVE_READ
        return AccessClass.READ
    if operation.startswith(_DELETE_PREFIXES):
        return AccessClass.DELETE
    return AccessClass.WRITE if operation[:1].isupper() else AccessClass.UNKNOWN


class ReadOnlyViolation(PermissionError):
    """A non-READ AWS operation was attempted through a read-only session."""


def _before_call(model: Any, **_: Any) -> None:
    service, operation = model.service_model.service_name, model.name
    access = classify(service, operation)
    if access is not AccessClass.READ:
        raise ReadOnlyViolation(f"{service}:{operation} is {access}; only READ is allowed here")


def read_only_session(profile: str | None, region: str | None) -> Any:
    """A boto3 session that refuses any non-READ call before it leaves the process.

    Credentials (including role assumption and refresh) come from a separate source session,
    so the guard never blocks the SDK's own credential calls.
    """
    import boto3
    import botocore.session

    source = boto3.Session(profile_name=profile or None, region_name=region)
    credentials = source.get_credentials()
    if credentials is None:
        raise PermissionError(f"no AWS credentials for profile {profile or 'default'!r}")
    core = botocore.session.Session()
    core._credentials = credentials
    if region or source.region_name:
        core.set_config_variable("region", region or source.region_name)
    core.register("before-call", _before_call, unique_id="mini-agent-read-guard")
    return boto3.Session(botocore_session=core)
