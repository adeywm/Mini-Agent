"""AWS execution broker: the ONLY component that can call mutating AWS APIs.

Every call re-checks, independently of the graph or the model:

1. the operation is part of the approved plan (same digest, same canonical content);
2. policy (``PolicyEngine``) does not DENY;
3. write switches: config ``execution.aws_writes_enabled`` AND process env
   ``INFRA_AGENT_ENABLE_AWS_WRITES`` AND the account's ``writes_enabled``;
4. if policy requires approval: an approved, consumed, unexpired approval for this plan
   digest whose scope includes this operation;
5. botocore shape validation;
6. idempotency: a SUCCEEDED record for the same fingerprint short-circuits.

Credentials come from the account's server-side ``execution_profile``; the model never
supplies a role, account or credentials. A per-call botocore guard allows exactly the one
approved operation. The attempt is persisted before the call; transport failures are
recorded AMBIGUOUS (never auto-retried), API rejections FAILED.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any

from infra_agent.aws import catalog
from infra_agent.aws.reader import _jsonable
from infra_agent.config import AccountConfig, AgentConfig, AppSettings, ConfigError
from infra_agent.core.operations import AccessClass
from infra_agent.models.domain import (
    AuditEvent,
    ChangePlan,
    Decision,
    ExecutionRecord,
    ExecutionStatus,
    PlannedOperation,
    sha256_digest,
    utcnow,
)
from infra_agent.persistence.store import Store
from infra_agent.policy.engine import PolicyEngine, access_of
from infra_agent.security.redaction import redact

SessionFactory = Callable[[AccountConfig], Any]


class WriteNotPermitted(PermissionError):
    pass


def operation_fingerprint(plan: ChangePlan, op: PlannedOperation) -> str:
    return sha256_digest({"plan": plan.digest, "op": op.model_dump(mode="json")})


def _default_session_factory(account: AccountConfig) -> Any:
    import boto3

    if not account.execution_profile:
        raise WriteNotPermitted("account has no execution_profile")
    return boto3.Session(profile_name=account.execution_profile)


class AwsExecutionBroker:
    def __init__(
        self,
        config: AgentConfig,
        settings: AppSettings,
        store: Store,
        session_factory: SessionFactory = _default_session_factory,
    ) -> None:
        self._cfg = config
        self._settings = settings
        self._store = store
        self._sessions = session_factory
        self._policy = PolicyEngine(config)

    def writes_enabled(self, account: AccountConfig) -> bool:
        return bool(
            self._cfg.execution.aws_writes_enabled
            and self._settings.enable_aws_writes
            and account.writes_enabled
        )

    def _block(
        self, plan: ChangePlan, op: PlannedOperation, fp: str, reason: str
    ) -> ExecutionRecord:
        record = ExecutionRecord(
            task_id=plan.task_id,
            operation_id=op.operation_id,
            plan_digest=plan.digest,
            operation_fingerprint=fp,
            status=ExecutionStatus.BLOCKED,
            reason=reason,
        )
        self._store.record_execution(record)
        self._audit(plan, op, "denied", reason)
        return record

    def _audit(self, plan: ChangePlan, op: PlannedOperation, outcome: str, detail: str) -> None:
        self._store.append_audit(
            [
                AuditEvent(
                    actor="execution-broker",
                    action=f"aws:{op.service}:{op.operation}",
                    target=f"{op.account_alias}/{op.region}",
                    outcome=outcome,  # type: ignore[arg-type]
                    correlation_id=plan.task_id,
                    payload_digest=plan.digest,
                    detail=detail[:500],
                )
            ]
        )

    def _approval_ok(
        self, plan: ChangePlan, op: PlannedOperation, approval_id: str | None
    ) -> str | None:
        if not approval_id:
            return "approval required but none supplied"
        approval = self._store.get_approval(approval_id)
        if approval is None:
            return "approval not found"
        if approval.task_id != plan.task_id or approval.plan_digest != plan.digest:
            return "approval does not match this plan"
        if approval.decision != "approved" or approval.used_at is None:
            return "approval not granted or not consumed by the workflow"
        if op.operation_id not in approval.scope:
            return "operation outside approval scope"
        ttl = timedelta(minutes=self._cfg.execution.approval_ttl_minutes)
        if utcnow() - approval.used_at > ttl or approval.expires_at < approval.used_at:
            return "approval expired"
        return None

    def execute(
        self, plan: ChangePlan, op: PlannedOperation, approval_id: str | None = None
    ) -> ExecutionRecord:
        fp = operation_fingerprint(plan, op)
        if op.provider != "aws":
            return self._block(plan, op, fp, "not an AWS operation")
        if op not in plan.operations:
            return self._block(plan, op, fp, "operation is not part of the plan")

        existing = self._store.succeeded_execution(fp)
        if existing is not None:
            return existing  # idempotent: never repeat a succeeded side effect

        decision = self._policy.evaluate(op, plan.digest)
        if decision.decision is Decision.DENY:
            return self._block(plan, op, fp, f"policy: {decision.reason}")
        try:
            account = self._cfg.account(op.account_alias)
        except ConfigError as exc:
            return self._block(plan, op, fp, str(exc))

        access = access_of(op)
        if access is not AccessClass.READ and self._store.writes_frozen():
            return self._block(plan, op, fp, "kill switch: writes are frozen")
        if access in (AccessClass.WRITE, AccessClass.DELETE) and not self.writes_enabled(account):
            return self._block(plan, op, fp, "writes disabled (config, env or account switch off)")
        if decision.decision is Decision.REQUIRE_APPROVAL:
            problem = self._approval_ok(plan, op, approval_id)
            if problem:
                return self._block(plan, op, fp, problem)

        try:
            catalog.validate_params(op.service, op.operation, op.parameters)
        except Exception as exc:
            return self._block(plan, op, fp, f"invalid parameters: {exc}")

        started = ExecutionRecord(
            task_id=plan.task_id,
            operation_id=op.operation_id,
            plan_digest=plan.digest,
            operation_fingerprint=fp,
            status=ExecutionStatus.STARTED,
            reason="attempt started",
            role_alias=account.alias,
        )
        self._store.record_execution(started)

        session = self._sessions(account)
        client = session.client(op.service, region_name=op.region)
        allowed = (op.service, op.operation)

        def only_this(model: Any, **_: Any) -> None:
            if (model.service_model.service_name, model.name) != allowed:
                raise WriteNotPermitted(f"unexpected call {model.name}")

        events = getattr(getattr(client, "meta", None), "events", None)
        if events is not None:
            events.register("before-call", only_this)

        try:
            response = getattr(client, catalog.method_name(op.service, op.operation))(
                **op.parameters
            )
        except Exception as exc:
            from botocore.exceptions import ClientError

            definite = isinstance(exc, ClientError | WriteNotPermitted)
            status = ExecutionStatus.FAILED if definite else ExecutionStatus.AMBIGUOUS
            reason = f"{type(exc).__name__}: {exc}"
            if not definite:
                reason += " - outcome unknown; reconcile by reading state before any retry"
            record = started.model_copy(
                update={
                    "id": f"{started.id}-r",
                    "status": status,
                    "reason": reason[:1000],
                    "recorded_at": utcnow(),
                }
            )
            self._store.record_execution(record)
            self._audit(plan, op, "error", reason)
            return record

        meta = response.get("ResponseMetadata", {}) if isinstance(response, dict) else {}
        record = started.model_copy(
            update={
                "id": f"{started.id}-r",
                "status": ExecutionStatus.SUCCEEDED,
                "reason": "API accepted the request",
                "external_request_id": meta.get("RequestId"),
                "result": redact(_jsonable(response)),
                "recorded_at": utcnow(),
            }
        )
        self._store.record_execution(record)
        self._audit(plan, op, "allowed", f"executed; request {record.external_request_id}")
        return record
