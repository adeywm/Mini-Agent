"""Durable task / evidence / approval / execution / audit records.

``PostgresStore`` is the production implementation. ``InMemoryStore`` exists for tests only
(AGENTS.md: in-memory state is acceptable only in tests). Both share the ``Store`` protocol.

Security-relevant invariants enforced here:

* audit events are append-only (database trigger rejects UPDATE/DELETE);
* an approval can be consumed exactly once, only if approved, unexpired and matching the
  plan digest (atomic UPDATE ... RETURNING);
* event deliveries are deduplicated by key;
* at most one SUCCEEDED execution per operation fingerprint (unique partial index).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from importlib import resources
from typing import Any, Protocol

from infra_agent.models.domain import (
    ApprovalRequest,
    AuditEvent,
    ChangePlan,
    Evidence,
    ExecutionRecord,
    ExecutionStatus,
    Task,
)


class DuplicateExecution(RuntimeError):
    """A SUCCEEDED execution already exists for this operation fingerprint."""


class Store(Protocol):
    def save_task(self, task: Task) -> None: ...
    def get_task(self, task_id: str) -> Task | None: ...
    def save_evidence(self, task_id: str, evidence: list[Evidence]) -> None: ...
    def append_audit(self, events: list[AuditEvent]) -> None: ...
    def list_audit(self, correlation_id: str) -> list[AuditEvent]: ...
    def save_approval(self, approval: ApprovalRequest) -> None: ...
    def get_approval(self, approval_id: str) -> ApprovalRequest | None: ...
    def consume_approval(self, approval_id: str, plan_digest: str) -> ApprovalRequest | None: ...
    def record_execution(self, record: ExecutionRecord) -> None: ...
    def succeeded_execution(self, fingerprint: str) -> ExecutionRecord | None: ...
    def register_event(self, dedupe_key: str, provider: str, payload: dict[str, Any]) -> bool: ...
    def save_plan(self, plan: ChangePlan) -> None: ...
    def get_plan(self, digest: str) -> ChangePlan | None: ...


def _now() -> datetime:
    return datetime.now(UTC)


class InMemoryStore:
    """Test double with the same invariants as PostgresStore."""

    def __init__(self) -> None:
        self.tasks: dict[str, Task] = {}
        self.evidence: dict[tuple[str, str], Evidence] = {}
        self.audit: list[AuditEvent] = []
        self.approvals: dict[str, ApprovalRequest] = {}
        self.executions: list[ExecutionRecord] = []
        self.inbox: set[str] = set()
        self.plans: dict[str, ChangePlan] = {}

    def save_plan(self, plan: ChangePlan) -> None:
        self.plans.setdefault(plan.digest, plan)

    def get_plan(self, digest: str) -> ChangePlan | None:
        return self.plans.get(digest)

    def save_task(self, task: Task) -> None:
        self.tasks[task.id] = task.model_copy(deep=True)

    def get_task(self, task_id: str) -> Task | None:
        t = self.tasks.get(task_id)
        return t.model_copy(deep=True) if t else None

    def save_evidence(self, task_id: str, evidence: list[Evidence]) -> None:
        for e in evidence:
            self.evidence[(task_id, e.id)] = e

    def append_audit(self, events: list[AuditEvent]) -> None:
        self.audit.extend(events)

    def list_audit(self, correlation_id: str) -> list[AuditEvent]:
        return [e for e in self.audit if e.correlation_id == correlation_id]

    def save_approval(self, approval: ApprovalRequest) -> None:
        self.approvals[approval.id] = approval

    def get_approval(self, approval_id: str) -> ApprovalRequest | None:
        return self.approvals.get(approval_id)

    def consume_approval(self, approval_id: str, plan_digest: str) -> ApprovalRequest | None:
        a = self.approvals.get(approval_id)
        if (
            a is None
            or a.decision != "approved"
            or a.used_at is not None
            or a.plan_digest != plan_digest
            or a.expires_at <= _now()
        ):
            return None
        used = a.model_copy(update={"used_at": _now()})
        self.approvals[approval_id] = used
        return used

    def record_execution(self, record: ExecutionRecord) -> None:
        if record.status is ExecutionStatus.SUCCEEDED and self.succeeded_execution(
            record.operation_fingerprint
        ):
            raise DuplicateExecution(record.operation_fingerprint)
        self.executions.append(record)

    def succeeded_execution(self, fingerprint: str) -> ExecutionRecord | None:
        for r in self.executions:
            if r.operation_fingerprint == fingerprint and r.status is ExecutionStatus.SUCCEEDED:
                return r
        return None

    def register_event(self, dedupe_key: str, provider: str, payload: dict[str, Any]) -> bool:
        if dedupe_key in self.inbox:
            return False
        self.inbox.add(dedupe_key)
        return True


class PostgresStore:
    def __init__(self, dsn: str) -> None:
        import psycopg

        self._dsn = dsn
        self._psycopg = psycopg

    def _conn(self) -> Any:
        return self._psycopg.connect(self._dsn, autocommit=True)

    def migrate(self) -> list[str]:
        """Apply bundled SQL migrations in order; idempotent."""
        applied: list[str] = []
        files = sorted(
            (
                f
                for f in resources.files("infra_agent.persistence.migrations").iterdir()
                if f.name.endswith(".sql")
            ),
            key=lambda f: f.name,
        )
        with self._conn() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
            done = {r[0] for r in conn.execute("SELECT name FROM schema_migrations").fetchall()}
            for f in files:
                if f.name in done:
                    continue
                with conn.transaction():
                    conn.execute(f.read_text(encoding="utf-8"))
                    conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (f.name,))
                applied.append(f.name)
        return applied

    def save_task(self, task: Task) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO tasks (id, requester, origin, intent, account_alias, region,
                       status, payload, created_at, last_updated)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status,
                       payload = EXCLUDED.payload, last_updated = EXCLUDED.last_updated""",
                (
                    task.id,
                    task.requester,
                    task.origin,
                    task.intent,
                    task.account_alias,
                    task.region,
                    task.status.value,
                    task.model_dump_json(),
                    task.created_at,
                    task.last_updated,
                ),
            )

    def get_task(self, task_id: str) -> Task | None:
        with self._conn() as conn:
            row = conn.execute("SELECT payload FROM tasks WHERE id = %s", (task_id,)).fetchone()
        return Task.model_validate(row[0]) if row else None

    def save_evidence(self, task_id: str, evidence: list[Evidence]) -> None:
        with self._conn() as conn, conn.transaction():
            for e in evidence:
                conn.execute(
                    """INSERT INTO evidence (task_id, id, source, digest, collected_at, payload)
                       VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                    (task_id, e.id, e.source, e.digest, e.collected_at, e.model_dump_json()),
                )

    def append_audit(self, events: list[AuditEvent]) -> None:
        with self._conn() as conn, conn.transaction():
            for e in events:
                conn.execute(
                    """INSERT INTO audit_events (id, correlation_id, actor, action, outcome,
                           occurred_at, payload) VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        e.id,
                        e.correlation_id,
                        e.actor,
                        e.action,
                        e.outcome,
                        e.timestamp,
                        e.model_dump_json(),
                    ),
                )

    def list_audit(self, correlation_id: str) -> list[AuditEvent]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT payload FROM audit_events WHERE correlation_id = %s ORDER BY seq",
                (correlation_id,),
            ).fetchall()
        return [AuditEvent.model_validate(r[0]) for r in rows]

    def save_approval(self, approval: ApprovalRequest) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO approvals (id, task_id, plan_digest, decision, requester,
                       approver, expires_at, used_at, payload)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (id) DO UPDATE SET decision = EXCLUDED.decision,
                       approver = EXCLUDED.approver, payload = EXCLUDED.payload
                   WHERE approvals.used_at IS NULL""",
                (
                    approval.id,
                    approval.task_id,
                    approval.plan_digest,
                    approval.decision,
                    approval.requester,
                    approval.approver,
                    approval.expires_at,
                    approval.used_at,
                    approval.model_dump_json(),
                ),
            )

    def get_approval(self, approval_id: str) -> ApprovalRequest | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT payload, used_at FROM approvals WHERE id = %s", (approval_id,)
            ).fetchone()
        if not row:
            return None
        return ApprovalRequest.model_validate(row[0]).model_copy(update={"used_at": row[1]})

    def consume_approval(self, approval_id: str, plan_digest: str) -> ApprovalRequest | None:
        with self._conn() as conn:
            row = conn.execute(
                """UPDATE approvals SET used_at = now()
                   WHERE id = %s AND plan_digest = %s AND decision = 'approved'
                     AND used_at IS NULL AND expires_at > now()
                   RETURNING payload, used_at""",
                (approval_id, plan_digest),
            ).fetchone()
        if not row:
            return None
        return ApprovalRequest.model_validate(row[0]).model_copy(update={"used_at": row[1]})

    def record_execution(self, record: ExecutionRecord) -> None:
        try:
            with self._conn() as conn:
                conn.execute(
                    """INSERT INTO execution_records (id, task_id, operation_id, plan_digest,
                           operation_fingerprint, status, recorded_at, payload)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        record.id,
                        record.task_id,
                        record.operation_id,
                        record.plan_digest,
                        record.operation_fingerprint,
                        record.status.value,
                        record.recorded_at,
                        record.model_dump_json(),
                    ),
                )
        except self._psycopg.errors.UniqueViolation as exc:
            raise DuplicateExecution(record.operation_fingerprint) from exc

    def succeeded_execution(self, fingerprint: str) -> ExecutionRecord | None:
        with self._conn() as conn:
            row = conn.execute(
                """SELECT payload FROM execution_records
                   WHERE operation_fingerprint = %s AND status = 'SUCCEEDED'""",
                (fingerprint,),
            ).fetchone()
        return ExecutionRecord.model_validate(row[0]) if row else None

    def register_event(self, dedupe_key: str, provider: str, payload: dict[str, Any]) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                """INSERT INTO event_inbox (dedupe_key, provider, received_at, payload)
                   VALUES (%s,%s,now(),%s) ON CONFLICT DO NOTHING RETURNING dedupe_key""",
                (dedupe_key, provider, json.dumps(payload)),
            ).fetchone()
        return row is not None

    def save_plan(self, plan: ChangePlan) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO plans (digest, task_id, payload) VALUES (%s,%s,%s) "
                "ON CONFLICT DO NOTHING",
                (plan.digest, plan.task_id, plan.model_dump_json(exclude={"digest"})),
            )

    def get_plan(self, digest: str) -> ChangePlan | None:
        with self._conn() as conn:
            row = conn.execute("SELECT payload FROM plans WHERE digest=%s", (digest,)).fetchone()
        if not row:
            return None
        plan = ChangePlan.model_validate(row[0])
        if plan.digest != digest:  # tamper check on stored plan
            raise ValueError("stored plan digest mismatch")
        return plan
