"""Deterministic policy decision point (PDP). Default deny; the model cannot override it.

Order of evaluation for one ``PlannedOperation``:

1. Access class from the deterministic classifier. UNKNOWN -> DENY.
2. Hard prohibitions (audit/security disabling, privilege escalation, credential minting,
   backup destruction). DENY; no rule or approval can override.
3. Scope: account configured, region allowed, provider/repository allowlisted. Else DENY.
4. Permission level of the account (L1 READ / L2 CREATE-UPDATE / L3 DELETE). Else DENY.
   SENSITIVE_READ is never granted by a level; it needs an explicit operation-specific rule.
5. Parameter risk: public exposure (0.0.0.0/0, Principal "*", disabling public-access
   blocks) forces REQUIRE_APPROVAL(high), never auto.
6. Operator rules (first DENY wins, then first matching rule).
7. Defaults: READ -> ALLOW_AUTO; WRITE -> REQUIRE_APPROVAL; DELETE -> REQUIRE_APPROVAL(high).
   Production WRITE/DELETE are never auto. Cost-creating writes without a bounded estimate
   within budget are never auto.
"""

from __future__ import annotations

import fnmatch
import json
from typing import Any

from infra_agent.aws.classifier import classify
from infra_agent.config import AgentConfig, ConfigError, PolicyRule
from infra_agent.core.operations import AccessClass
from infra_agent.core.permissions import PermissionLevel, has_permission
from infra_agent.github.classifier import classify_github, repo_of
from infra_agent.models.domain import Decision, PlannedOperation, PolicyDecision

# (service, operation-glob) that are never executed by the agent.
PROHIBITED: tuple[tuple[str, str, str], ...] = (
    ("cloudtrail", "StopLogging", "disables audit logging"),
    ("cloudtrail", "DeleteTrail", "removes audit trail"),
    ("cloudtrail", "PutEventSelectors", "can silence audit logging"),
    ("config", "StopConfigurationRecorder", "disables AWS Config"),
    ("config", "DeleteConfigurationRecorder", "disables AWS Config"),
    ("config", "DeleteDeliveryChannel", "disables AWS Config"),
    ("guardduty", "DeleteDetector", "disables threat detection"),
    ("guardduty", "UpdateDetector", "can disable threat detection"),
    ("securityhub", "DisableSecurityHub", "disables Security Hub"),
    ("iam", "CreateAccessKey", "mints long-term credentials"),
    ("iam", "CreateLoginProfile", "creates console password"),
    ("iam", "UpdateLoginProfile", "changes console password"),
    ("iam", "Attach*Policy", "privilege escalation"),
    ("iam", "Put*Policy", "privilege escalation"),
    ("iam", "CreatePolicyVersion", "privilege escalation"),
    ("iam", "SetDefaultPolicyVersion", "privilege escalation"),
    ("iam", "UpdateAssumeRolePolicy", "privilege escalation"),
    ("iam", "AddUserToGroup", "privilege escalation"),
    ("iam", "Delete*PermissionsBoundary", "removes permission boundary"),
    ("iam", "PassRole", "role passing"),
    ("sts", "*", "role assumption is done by the broker, never requested by the agent"),
    ("organizations", "*", "organization-level changes"),
    ("kms", "ScheduleKeyDeletion", "irreversible key destruction"),
    ("kms", "DisableKey", "can make data unreadable"),
    ("kms", "PutKeyPolicy", "key policy changes"),
    ("backup", "DeleteBackupVault", "backup destruction"),
    ("backup", "DeleteRecoveryPoint", "backup destruction"),
    ("rds", "Delete*Snapshot", "backup destruction"),
    ("ec2", "DeleteSnapshot", "backup destruction"),
    ("s3", "DeleteBucketPolicy", "can remove protective bucket policy"),
    ("account", "*", "account-level changes"),
)

GITHUB_PROHIBITED: tuple[tuple[str, str], ...] = (
    ("DELETE", "/repos/*/*"),  # repository deletion
    ("*", "/repos/*/*/branches/*/protection*"),  # weakening branch protection
    ("*", "/repos/*/*/rulesets*"),
    ("*", "/orgs/*/members*"),
    ("*", "/orgs/*/memberships*"),
    ("PUT", "/repos/*/*/collaborators/*"),
    ("*", "/repos/*/*/actions/secrets*"),
    ("*", "/orgs/*/actions/secrets*"),
    ("*", "/repos/*/*/keys*"),  # deploy keys
    ("*", "/repos/*/*/hooks*"),
    ("*", "/orgs/*/hooks*"),
)

_COST_PREFIXES = ("Create", "Run", "Start", "Allocate", "Purchase", "Request", "Launch")
_PUBLIC_MARKERS = ("0.0.0.0/0", "::/0")


def _is_public_exposure(params: dict[str, Any]) -> bool:
    blob = json.dumps(params, default=str)
    if any(m in blob for m in _PUBLIC_MARKERS):
        return True
    compact = blob.replace(" ", "")
    if '"Principal":"*"' in compact or '"AWS":"*"' in compact:
        return True
    pab = params.get("PublicAccessBlockConfiguration")
    if isinstance(pab, dict) and any(v is False for v in pab.values()):
        return True
    return (
        params.get("ACL") in ("public-read", "public-read-write") or params.get("private") is False
    )


def _rule_matches(rule: PolicyRule, op: PlannedOperation, access: AccessClass) -> bool:
    checks: list[bool] = [
        rule.provider is None or rule.provider == op.provider,
        rule.account_alias is None or rule.account_alias == op.account_alias,
        rule.environment is None or rule.environment == op.environment,
        rule.service is None or rule.service == op.service,
        rule.operation is None or fnmatch.fnmatchcase(op.operation, rule.operation),
        rule.access is None or rule.access == access.value,
        rule.repository is None or rule.repository == op.parameters.get("repository"),
    ]
    return all(checks)


def access_of(op: PlannedOperation) -> AccessClass:
    if op.provider == "aws":
        return classify(op.service, op.operation)
    return classify_github(op.service, op.operation)


def _prohibited(op: PlannedOperation) -> str | None:
    if op.provider == "aws":
        for service, pattern, why in PROHIBITED:
            if op.service == service and fnmatch.fnmatchcase(op.operation, pattern):
                return why
        return None
    method, path = op.service.upper(), op.operation
    for m, pattern in GITHUB_PROHIBITED:
        if (m == "*" or m == method) and fnmatch.fnmatchcase(path, pattern):
            # Repository deletion pattern only for the bare repo path.
            if m == "DELETE" and pattern == "/repos/*/*" and path.count("/") != 3:
                continue
            return "protected GitHub administration or secret operation"
    return None


class PolicyEngine:
    def __init__(self, config: AgentConfig) -> None:
        self._cfg = config

    def evaluate(self, op: PlannedOperation, plan_digest: str) -> PolicyDecision:
        def decide(
            decision: Decision, reason: str, rules: tuple[str, ...] = (), level: str = "none"
        ) -> PolicyDecision:
            return PolicyDecision(
                plan_digest=plan_digest,
                operation_id=op.operation_id,
                decision=decision,
                matched_rules=rules,
                reason=reason,
                required_approval_level=level,  # type: ignore[arg-type]
            )

        access = access_of(op)
        if access is AccessClass.UNKNOWN:
            return decide(Decision.DENY, "unknown operation class")

        why = _prohibited(op)
        if why:
            return decide(Decision.DENY, f"prohibited: {why}", ("builtin:prohibited",))

        try:
            account = self._cfg.account(op.account_alias)
        except ConfigError as exc:
            return decide(Decision.DENY, str(exc))
        if op.environment != account.environment:
            return decide(Decision.DENY, "operation environment does not match account")
        if op.provider == "aws" and op.region not in account.allowed_regions:
            return decide(Decision.DENY, f"region {op.region} not allowed")
        if op.provider == "github":
            repo = op.parameters.get("repository")
            if repo_of(op.operation) != repo:
                return decide(Decision.DENY, "GitHub path must target the declared repository")
            try:
                mapped = self._cfg.repository(str(repo))
            except ConfigError as exc:
                return decide(Decision.DENY, str(exc))
            if mapped.account_alias != account.alias:
                return decide(Decision.DENY, "repository not mapped to this account")

        level = PermissionLevel(account.permission_level)
        rules = self._cfg.policy.rules
        matching = [r for r in rules if _rule_matches(r, op, access)]

        if access is AccessClass.SENSITIVE_READ:
            explicit = [
                r
                for r in matching
                if r.effect != "DENY"
                and r.operation
                and "*" not in r.operation
                and r.service == op.service
            ]
            if not explicit or any(r.effect == "DENY" for r in matching):
                return decide(Decision.DENY, "sensitive read requires an explicit grant")
            return decide(Decision.REQUIRE_APPROVAL, "sensitive read", (explicit[0].id,), "high")

        if not has_permission(level, access):
            return decide(
                Decision.DENY,
                f"{access} requires level {access_level_name(access)}; account has L{int(level)}",
            )

        public = op.provider == "aws" and _is_public_exposure(op.parameters)
        for r in matching:
            if r.effect == "DENY":
                return decide(Decision.DENY, f"denied by rule {r.id}", (r.id,))

        is_prod = op.environment == "production"
        high = access is AccessClass.DELETE or is_prod or public
        first = matching[0] if matching else None

        if first is not None and first.effect == "ALLOW_AUTO":
            if access is AccessClass.READ:
                return decide(Decision.ALLOW_AUTO, f"allowed by rule {first.id}", (first.id,))
            if is_prod or public or (access is AccessClass.DELETE and is_prod):
                return decide(
                    Decision.REQUIRE_APPROVAL,
                    "auto rule cannot cover high risk",
                    (first.id,),
                    "high",
                )
            if op.operation.startswith(_COST_PREFIXES):
                cost = op.estimated_cost_change_usd_monthly
                budget = self._cfg.budgets.max_estimated_new_monthly_usd
                if cost is None or cost > budget:
                    return decide(
                        Decision.REQUIRE_APPROVAL,
                        "cost unknown or above budget",
                        (first.id,),
                        "standard",
                    )
            return decide(Decision.ALLOW_AUTO, f"allowed by rule {first.id}", (first.id,))
        if first is not None and first.effect == "REQUIRE_APPROVAL":
            return decide(
                Decision.REQUIRE_APPROVAL,
                f"rule {first.id}",
                (first.id,),
                "high" if high else "standard",
            )

        if access is AccessClass.READ:
            return decide(Decision.ALLOW_AUTO, "read within scope")
        return decide(
            Decision.REQUIRE_APPROVAL,
            "default: changes need approval" + (" (public exposure)" if public else ""),
            (),
            "high" if high else "standard",
        )


def access_level_name(access: AccessClass) -> str:
    return {AccessClass.READ: "L1", AccessClass.WRITE: "L2", AccessClass.DELETE: "L3"}.get(
        access, "none"
    )
