"""Deterministic access classification for any AWS API operation.

Name prefixes are a *starting point*, not proof of safety (docs/AWS_AND_GITHUB_INTEGRATIONS.md).
Defence in depth for reads:

1. this classifier (unknown -> not READ, explicit sensitive denylist),
2. the botocore guard that refuses non-READ operations in-process,
3. the read identity, which ``security.credentials`` proves cannot mutate (the real boundary).
"""

from __future__ import annotations

import re

from infra_agent.core.operations import AccessClass

_READ_PREFIXES = ("Describe", "List", "Get", "BatchGet", "Search", "Lookup", "Head", "Filter")

# Operations that read secret material, mint credentials or return data-plane contents.
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

# Read-prefixed names that are known to change state or are otherwise unsafe.
_NOT_READ: frozenset[tuple[str, str]] = frozenset(
    {
        ("sqs", "ReceiveMessage"),  # changes message visibility (not prefixed, listed for clarity)
        ("ec2", "GetConsoleScreenshot"),
    }
)


# Non-prefixed operations that are pure evaluation/read.
_EXTRA_READ: frozenset[tuple[str, str]] = frozenset(
    {
        ("iam", "SimulatePrincipalPolicy"),
        ("iam", "SimulateCustomPolicy"),
    }
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
    return AccessClass.WRITE if operation[:1].isupper() else AccessClass.UNKNOWN
