"""Store invariants, run against InMemoryStore and (if configured) a real PostgreSQL.

Set INFRA_AGENT_TEST_DATABASE_URL to a disposable database to include Postgres.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import timedelta

import pytest

from infra_agent.models.domain import (
    ApprovalRequest,
    AuditEvent,
    ExecutionRecord,
    ExecutionStatus,
    Task,
    TaskStatus,
    new_id,
    utcnow,
)
from infra_agent.persistence.store import DuplicateExecution, InMemoryStore, PostgresStore, Store

PG_URL = os.environ.get("INFRA_AGENT_TEST_DATABASE_URL")


@pytest.fixture(params=["memory", "postgres"])
def store(request: pytest.FixtureRequest) -> Iterator[Store]:
    if request.param == "memory":
        yield InMemoryStore()
        return
    if not PG_URL:
        pytest.skip("INFRA_AGENT_TEST_DATABASE_URL not set")
    pg = PostgresStore(PG_URL)
    pg.migrate()
    yield pg


def _task() -> Task:
    return Task(requester="op", origin="cli", intent="x", account_alias="dev", region="ap-south-1")


def _approval(task: Task, **over: object) -> ApprovalRequest:
    base: dict[str, object] = {
        "id": new_id("apr"),
        "plan_digest": "sha256:abc",
        "task_id": task.id,
        "scope": ("ecs:dev-cluster/api",),
        "requester": "agent",
        "approver": "alice",
        "decision": "approved",
        "expires_at": utcnow() + timedelta(minutes=10),
    }
    base.update(over)
    return ApprovalRequest.model_validate(base)


def test_task_roundtrip_and_status_update(store: Store) -> None:
    t = _task()
    store.save_task(t)
    t.transition(TaskStatus.TRIAGING)
    store.save_task(t)
    loaded = store.get_task(t.id)
    assert loaded is not None and loaded.status is TaskStatus.TRIAGING
    assert store.get_task("missing") is None


def test_audit_is_ordered(store: Store) -> None:
    cid = new_id("corr")
    events = [
        AuditEvent(actor="a", action=f"step{i}", target="t", outcome="info", correlation_id=cid)
        for i in range(3)
    ]
    store.append_audit(events)
    assert [e.action for e in store.list_audit(cid)] == ["step0", "step1", "step2"]


def test_approval_consumed_exactly_once(store: Store) -> None:
    t = _task()
    store.save_task(t)
    a = _approval(t)
    store.save_approval(a)
    assert store.consume_approval(a.id, "sha256:other") is None  # wrong plan
    used = store.consume_approval(a.id, "sha256:abc")
    assert used is not None and used.used_at is not None
    assert store.consume_approval(a.id, "sha256:abc") is None  # replay


@pytest.mark.parametrize(
    "over",
    [
        {"decision": "rejected"},
        {"decision": "pending"},
        {"expires_at": utcnow() - timedelta(seconds=1)},
    ],
)
def test_unusable_approvals(store: Store, over: dict[str, object]) -> None:
    t = _task()
    store.save_task(t)
    a = _approval(t, **over)
    store.save_approval(a)
    assert store.consume_approval(a.id, "sha256:abc") is None


def test_single_successful_execution_per_fingerprint(store: Store) -> None:
    fp = new_id("fp")

    def rec(status: ExecutionStatus) -> ExecutionRecord:
        return ExecutionRecord(
            task_id="t",
            operation_id="op",
            plan_digest="d",
            operation_fingerprint=fp,
            status=status,
            reason="",
        )

    store.record_execution(rec(ExecutionStatus.FAILED))
    store.record_execution(rec(ExecutionStatus.SUCCEEDED))
    with pytest.raises(DuplicateExecution):
        store.record_execution(rec(ExecutionStatus.SUCCEEDED))
    assert store.succeeded_execution(fp) is not None


def test_event_dedupe(store: Store) -> None:
    key = new_id("delivery")
    assert store.register_event(key, "github", {"a": 1}) is True
    assert store.register_event(key, "github", {"a": 1}) is False


@pytest.mark.skipif(not PG_URL, reason="INFRA_AGENT_TEST_DATABASE_URL not set")
def test_postgres_audit_append_only() -> None:
    import psycopg

    pg = PostgresStore(PG_URL or "")
    pg.migrate()
    cid = new_id("corr")
    pg.append_audit(
        [AuditEvent(actor="a", action="x", target="t", outcome="info", correlation_id=cid)]
    )
    with psycopg.connect(PG_URL or "", autocommit=True) as conn:
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute("UPDATE audit_events SET outcome='denied' WHERE correlation_id=%s", (cid,))
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute("DELETE FROM audit_events WHERE correlation_id=%s", (cid,))
