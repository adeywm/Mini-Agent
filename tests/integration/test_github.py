"""GitHub reader / broker / app tokens with httpx.MockTransport (no network)."""

from __future__ import annotations

import copy
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from infra_agent.config import AgentConfig, AppSettings
from infra_agent.core.operations import OperationDenied
from infra_agent.github.client import (
    GitHubAppTokenProvider,
    GitHubExecutionBroker,
    GitHubReader,
    permissions_for,
    verify_github,
)
from infra_agent.models.domain import (
    ApprovalRequest,
    ChangePlan,
    ExecutionStatus,
    PlannedOperation,
    VerificationSpec,
    utcnow,
)
from infra_agent.persistence.store import InMemoryStore
from tests.conftest import config_dict

REPO = "example-org/backend"
GH_TOKEN = "ghp_" + "b" * 36


def _cfg(writes: bool = True, rules: list[dict[str, Any]] | None = None) -> AgentConfig:
    data = copy.deepcopy(config_dict())
    data["accounts"][0]["permission_level"] = 3
    data["execution"] = {"github_writes_enabled": writes}
    data["policy"] = {"rules": rules or []}
    return AgentConfig.model_validate(data)


class Recorder:
    def __init__(self, routes: dict[tuple[str, str], tuple[int, Any]]) -> None:
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status, body = self.routes.get((request.method, request.url.path), (404, {"message": "nf"}))
        return httpx.Response(status, json=body, headers={"x-github-request-id": "gh-req-1"})


def test_reader_scoped_and_redacted() -> None:
    rec = Recorder(
        {
            ("GET", f"/repos/{REPO}/actions/runs"): (
                200,
                {
                    "workflow_runs": [
                        {"id": 1, "conclusion": "failure", "note": f"token {GH_TOKEN}"}
                    ]
                },
            )
        }
    )
    reader = GitHubReader(_cfg(), "read-token", httpx.MockTransport(rec))
    data = reader.get(f"/repos/{REPO}/actions/runs")
    assert data["workflow_runs"][0]["conclusion"] == "failure"
    assert GH_TOKEN not in json.dumps(data)
    assert rec.requests[0].headers["Authorization"] == "Bearer read-token"
    for bad in (
        f"/repos/{REPO}/actions/secrets",
        "/repos/attacker/repo/contents",
        "/user",
        f"/repos/{REPO}/../../orgs/x",
    ):
        with pytest.raises(OperationDenied):
            reader.get(bad)
    assert len(rec.requests) == 1


def _op(
    method: str, path: str, body: dict[str, Any] | None = None, **over: Any
) -> PlannedOperation:
    return PlannedOperation(
        operation_id="op-001",
        provider="github",
        account_alias="dev",
        environment="development",
        service=method,
        operation=path,
        region="ap-south-1",
        resource_scope=(REPO,),
        parameters={"repository": REPO, "body": body or {}},
        expected_outcome="pr",
        verification="-",
        idempotency_key="k",
        **over,
    )


def _broker(
    cfg: AgentConfig, rec: Recorder, env: bool = True
) -> tuple[GitHubExecutionBroker, InMemoryStore, list[Any]]:
    store = InMemoryStore()
    minted: list[Any] = []

    def tokens(repo: str, perms: dict[str, str]) -> str:
        minted.append((repo, perms))
        return "inst-token"

    broker = GitHubExecutionBroker(
        cfg, AppSettings(enable_github_writes=env), store, tokens, httpx.MockTransport(rec)
    )
    return broker, store, minted


def test_pr_creation_with_approval_and_idempotency() -> None:
    rec = Recorder({("POST", f"/repos/{REPO}/pulls"): (201, {"number": 7, "state": "open"})})
    broker, store, minted = _broker(_cfg(), rec)
    op = _op("POST", f"/repos/{REPO}/pulls", {"title": "fix", "head": "fix", "base": "main"})
    plan = ChangePlan(id="p", version=1, task_id="t", operations=(op,))
    assert broker.execute(plan, op).status is ExecutionStatus.BLOCKED  # approval needed
    store.save_approval(
        ApprovalRequest(
            id="a1",
            plan_digest=plan.digest,
            task_id="t",
            scope=("op-001",),
            requester="bob",
            approver="alice",
            decision="approved",
            expires_at=utcnow() + timedelta(minutes=5),
        )
    )
    store.consume_approval("a1", plan.digest)
    first = broker.execute(plan, op, "a1")
    again = broker.execute(plan, op, "a1")
    assert first.status is ExecutionStatus.SUCCEEDED and first.result == {
        "number": 7,
        "state": "open",
    }
    assert again.id == first.id and len(rec.requests) == 1
    assert minted == [(REPO, {"pull_requests": "write"})]
    assert rec.requests[0].headers["Authorization"] == "Bearer inst-token"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("DELETE", f"/repos/{REPO}"),
        ("PUT", f"/repos/{REPO}/branches/main/protection"),
        ("PUT", f"/repos/{REPO}/actions/secrets/X"),
        ("POST", "/repos/attacker/repo/pulls"),
    ],
)
def test_protected_github_writes_blocked(method: str, path: str) -> None:
    rec = Recorder({})
    broker, _, minted = _broker(_cfg(rules=[{"id": "all", "effect": "ALLOW_AUTO"}]), rec)
    op = _op(method, path)
    plan = ChangePlan(id="p", version=1, task_id="t", operations=(op,))
    assert broker.execute(plan, op).status is ExecutionStatus.BLOCKED
    assert rec.requests == [] and minted == []


def test_switches_and_http_failures() -> None:
    allow = [{"id": "auto", "effect": "ALLOW_AUTO", "provider": "github"}]
    op = _op("POST", f"/repos/{REPO}/issues", {"title": "x"})
    plan = ChangePlan(id="p", version=1, task_id="t", operations=(op,))
    for cfg_on, env_on in ((False, True), (True, False)):
        broker, _, _ = _broker(_cfg(cfg_on, allow), Recorder({}), env_on)
        assert broker.execute(plan, op).status is ExecutionStatus.BLOCKED
    broker, _, _ = _broker(
        _cfg(True, allow), Recorder({("POST", f"/repos/{REPO}/issues"): (422, {"message": "bad"})})
    )
    assert broker.execute(plan, op).status is ExecutionStatus.FAILED

    def boom(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timeout")

    store = InMemoryStore()
    b = GitHubExecutionBroker(
        _cfg(True, allow),
        AppSettings(enable_github_writes=True),
        store,
        lambda r, p: "t",
        httpx.MockTransport(boom),
    )
    assert b.execute(plan, op).status is ExecutionStatus.AMBIGUOUS


def test_verify_and_permissions() -> None:
    rec = Recorder({("GET", f"/repos/{REPO}/pulls/7"): (200, {"state": "open"})})
    reader = GitHubReader(_cfg(), None, httpx.MockTransport(rec))
    op = _op(
        "POST",
        f"/repos/{REPO}/pulls",
        verify=VerificationSpec(
            service="GET", operation=f"/repos/{REPO}/pulls/7", expect={"state": "open"}
        ),
    )
    assert verify_github(reader, op)[0] is True
    assert permissions_for(f"/repos/{REPO}/contents/a.txt") == {"contents": "write"}


def test_app_token_is_repo_scoped(tmp_path: Path) -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = tmp_path / "app.pem"
    pem.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        assert request.headers["Authorization"].startswith("Bearer ey")
        return httpx.Response(201, json={"token": "ghs_x"})

    provider = GitHubAppTokenProvider(
        1, 2, pem, "https://api.github.com", httpx.MockTransport(handler)
    )
    assert provider.token_for(REPO, {"pull_requests": "write"}) == "ghs_x"
    assert seen == [{"repositories": ["backend"], "permissions": {"pull_requests": "write"}}]
