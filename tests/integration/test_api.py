from __future__ import annotations

import copy
import hashlib
import hmac
import json
from collections.abc import Iterator
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from infra_agent.api.main import app, get_platform
from infra_agent.config import AgentConfig, AppSettings
from infra_agent.models.domain import (
    ApprovalRequest,
    ChangePlan,
    PlannedOperation,
    Task,
    TaskStatus,
    utcnow,
)
from infra_agent.persistence.queue import InMemoryQueue
from infra_agent.persistence.store import InMemoryStore
from infra_agent.runtime import Platform
from tests.conftest import config_dict


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@pytest.fixture
def platform() -> Iterator[Platform]:
    data = copy.deepcopy(config_dict())
    data["api"] = {
        "users": [
            {"name": "bob", "roles": ["operator"], "token_sha256": _sha("bob-token")},
            {"name": "alice", "roles": ["approver"], "token_sha256": _sha("alice-token")},
        ]
    }
    data["triggers"] = [
        {
            "id": "gh",
            "source": "github",
            "event": "workflow_run",
            "match": {"workflow_run.conclusion": "failure"},
            "account_alias": "dev",
            "intent": "Investigate failure",
        }
    ]
    p = Platform(
        config=AgentConfig.model_validate(data),
        settings=AppSettings(app_env="test", github_webhook_secret="whsec"),
        store=InMemoryStore(),
        queue=InMemoryQueue(),
    )
    app.dependency_overrides[get_platform] = lambda: p
    yield p
    app.dependency_overrides.clear()


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_auth_and_roles(platform: Platform) -> None:
    c = TestClient(app)
    body = {"intent": "inspect ec2", "account_alias": "dev", "region": "ap-south-1"}
    assert c.post("/v1/tasks", json=body).status_code == 401
    assert c.post("/v1/tasks", json=body, headers=_h("wrong")).status_code == 401
    assert c.post("/v1/tasks", json=body, headers=_h("alice-token")).status_code == 403
    r = c.post("/v1/tasks", json=body, headers=_h("bob-token"))
    assert r.status_code == 202
    task_id = r.json()["task_id"]
    assert platform.queue.jobs[0]["task_id"] == task_id  # type: ignore[attr-defined]
    assert c.get(f"/v1/tasks/{task_id}", headers=_h("alice-token")).json()["status"] == "RECEIVED"
    bad = {**body, "region": "us-east-1"}
    assert c.post("/v1/tasks", json=bad, headers=_h("bob-token")).status_code == 422
    extra = {**body, "role_arn": "arn:aws:iam::111122223333:role/Admin"}
    assert c.post("/v1/tasks", json=extra, headers=_h("bob-token")).status_code == 422


def test_approval_review_and_decision(platform: Platform) -> None:
    task = Task(requester="bob", origin="api", intent="x", account_alias="dev", region="ap-south-1")
    platform.store.save_task(task)
    op = PlannedOperation(
        operation_id="op-001",
        provider="aws",
        account_alias="dev",
        environment="development",
        service="ecs",
        operation="UpdateService",
        region="ap-south-1",
        resource_scope=("x",),
        parameters={},
        expected_outcome="-",
        verification="-",
        idempotency_key="k",
    )
    plan = ChangePlan(id="p", version=1, task_id=task.id, operations=(op,))
    platform.store.save_plan(plan)
    platform.store.save_approval(
        ApprovalRequest(
            id="apr-1",
            plan_digest=plan.digest,
            task_id=task.id,
            scope=("op-001",),
            requester="bob",
            expires_at=utcnow() + timedelta(minutes=10),
        )
    )
    c = TestClient(app)
    view = c.get("/v1/approvals/apr-1", headers=_h("alice-token")).json()
    assert view["plan"]["operations"][0]["operation"] == "UpdateService"
    dec = {"decision": "approve", "plan_digest": plan.digest}
    assert (
        c.post("/v1/approvals/apr-1/decision", json=dec, headers=_h("bob-token")).status_code == 403
    )
    wrong = {**dec, "plan_digest": "sha256:x"}
    assert (
        c.post("/v1/approvals/apr-1/decision", json=wrong, headers=_h("alice-token")).status_code
        == 409
    )
    r = c.post("/v1/approvals/apr-1/decision", json=dec, headers=_h("alice-token"))
    assert r.json()["decision"] == "approved"
    assert platform.queue.jobs[-1]["kind"] == "resume"  # type: ignore[attr-defined]
    again = c.post("/v1/approvals/apr-1/decision", json=dec, headers=_h("alice-token"))
    assert again.status_code == 409


def test_cancel(platform: Platform) -> None:
    c = TestClient(app)
    r = c.post(
        "/v1/tasks",
        json={"intent": "inspect", "account_alias": "dev", "region": "ap-south-1"},
        headers=_h("bob-token"),
    )
    tid = r.json()["task_id"]
    assert (
        c.post(f"/v1/tasks/{tid}/cancel", headers=_h("bob-token")).json()["status"] == "CANCELLED"
    )
    assert c.post(f"/v1/tasks/{tid}/cancel", headers=_h("bob-token")).status_code == 409
    stored = platform.store.get_task(tid)
    assert stored is not None and stored.status is TaskStatus.CANCELLED


def test_github_webhook(platform: Platform) -> None:
    c = TestClient(app)
    payload = {
        "workflow_run": {"conclusion": "failure"},
        "repository": {"full_name": "example-org/backend"},
    }
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(b"whsec", body, hashlib.sha256).hexdigest()
    headers = {
        "X-Hub-Signature-256": sig,
        "X-GitHub-Event": "workflow_run",
        "X-GitHub-Delivery": "d-1",
        "Content-Type": "application/json",
    }
    assert (
        c.post(
            "/v1/webhooks/github",
            content=body,
            headers={**headers, "X-Hub-Signature-256": "sha256=" + "0" * 64},
        ).status_code
        == 401
    )
    first = c.post("/v1/webhooks/github", content=body, headers=headers).json()
    assert len(first["tasks"]) == 1
    assert c.post("/v1/webhooks/github", content=body, headers=headers).json()["duplicate"] is True


def test_kill_switch_requires_admin(platform: Platform) -> None:
    import dataclasses

    users = (
        *platform.config.api.users,
        platform.config.api.users[0].model_copy(
            update={"name": "root-op", "roles": ("admin",), "token_sha256": _sha("admin-token")}
        ),
    )
    platform.config = platform.config.model_copy(
        update={"api": platform.config.api.model_copy(update={"users": users})}
    )
    assert dataclasses.is_dataclass(platform)
    c = TestClient(app)
    assert (
        c.post("/v1/admin/kill-switch", json={"frozen": True}, headers=_h("bob-token")).status_code
        == 403
    )
    r = c.post("/v1/admin/kill-switch", json={"frozen": True}, headers=_h("admin-token"))
    assert r.json() == {"writes_frozen": True}
