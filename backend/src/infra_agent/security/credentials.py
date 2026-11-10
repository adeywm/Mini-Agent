"""Validate the read identity before any live AWS read is allowed.

Checks, in order (all must pass):

1. ``sts:GetCallerIdentity`` succeeds and the account matches the operator-configured
   account ID for the alias. A different account is refused, never "adopted".
2. The principal is not the account root user.
3. IAM policy simulation proves a set of probe mutations is denied for the principal
   (``require_mutation_denial_proof``; on by default). If simulation is not permitted
   the check fails closed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from infra_agent.config import AccountConfig
from infra_agent.security import read_guard
from infra_agent.security.redaction import mask_account_ids

# Representative mutations across services. Any "allowed" result means the identity is not
# read-only and live reads are refused.
PROBE_MUTATIONS: tuple[str, ...] = (
    "ecs:UpdateService",
    "ecs:DeleteService",
    "ecs:RegisterTaskDefinition",
    "ec2:TerminateInstances",
    "ec2:AuthorizeSecurityGroupIngress",
    "s3:DeleteBucket",
    "s3:PutBucketPolicy",
    "rds:DeleteDBInstance",
    "iam:CreatePolicyVersion",
    "iam:AttachRolePolicy",
    "iam:PassRole",
    "sts:AssumeRole",
    "cloudtrail:StopLogging",
    "lambda:UpdateFunctionCode",
    "secretsmanager:GetSecretValue",
)

_ASSUMED_ROLE_RE = re.compile(r"^arn:aws[a-z-]*:sts::(\d{12}):assumed-role/([^/]+)/.+$")
_IAM_USER_RE = re.compile(r"^arn:aws[a-z-]*:iam::(\d{12}):user/.+$")


@dataclass
class IdentityCheck:
    account_alias: str
    status: Literal["passed", "failed"]
    principal_arn: str | None = None
    reasons: list[str] = field(default_factory=list)
    allowed_mutations: list[str] = field(default_factory=list)
    simulation: Literal["passed", "failed", "skipped", "unavailable"] = "skipped"

    @property
    def ok(self) -> bool:
        return self.status == "passed"

    @property
    def display_arn(self) -> str | None:
        return mask_account_ids(self.principal_arn) if self.principal_arn else None


class CredentialValidationError(PermissionError):
    def __init__(self, check: IdentityCheck) -> None:
        super().__init__("; ".join(check.reasons) or "credential validation failed")
        self.check = check


def _policy_source_arn(iam: Any, principal_arn: str) -> str | None:
    if _IAM_USER_RE.match(principal_arn):
        return principal_arn
    m = _ASSUMED_ROLE_RE.match(principal_arn)
    if not m:
        return None
    # The role path (e.g. aws-reserved/sso.amazonaws.com/...) is not in the session ARN.
    return str(iam.get_role(RoleName=m.group(2))["Role"]["Arn"])


def validate_identity(session: Any, account: AccountConfig) -> IdentityCheck:
    """Run all checks with a guarded session. Never raises for AWS errors; reports them."""
    read_guard.install(session)
    check = IdentityCheck(account_alias=account.alias, status="failed")

    try:
        ident = session.client("sts").get_caller_identity()
    except Exception as exc:
        check.reasons.append(f"sts:GetCallerIdentity failed: {type(exc).__name__}")
        return check

    arn = str(ident.get("Arn", ""))
    check.principal_arn = arn
    if str(ident.get("Account")) != account.account_id:
        check.reasons.append(
            f"credentials belong to a different account than alias {account.alias!r}"
        )
        return check
    if arn.endswith(":root"):
        check.reasons.append("root user credentials are refused")
        return check

    if not account.require_mutation_denial_proof:
        check.simulation = "skipped"
        check.status = "passed"
        check.reasons.append("WARNING: mutation-denial proof disabled for this account")
        return check

    try:
        iam = session.client("iam")
        source_arn = _policy_source_arn(iam, arn)
        if source_arn is None:
            raise ValueError("unsupported principal type for simulation")
        allowed: list[str] = []
        paginator_kwargs = {"PolicySourceArn": source_arn, "ActionNames": list(PROBE_MUTATIONS)}
        result = iam.simulate_principal_policy(**paginator_kwargs)
        for item in result.get("EvaluationResults", []):
            if item.get("EvalDecision") == "allowed":
                allowed.append(str(item.get("EvalActionName")))
    except Exception as exc:
        check.simulation = "unavailable"
        check.reasons.append(
            f"IAM policy simulation unavailable ({type(exc).__name__}); grant the read role "
            "iam:GetRole and iam:SimulatePrincipalPolicy on itself, or disable "
            "require_mutation_denial_proof for this account"
        )
        return check

    check.allowed_mutations = sorted(allowed)
    if allowed:
        check.simulation = "failed"
        check.reasons.append(
            "identity is not read-only; allowed probe actions: " + ", ".join(sorted(allowed))
        )
        return check

    check.simulation = "passed"
    check.status = "passed"
    return check
