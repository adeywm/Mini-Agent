from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from infra_agent.agentcore_app import app
from infra_agent.config import AgentConfig, AppSettings
from infra_agent.persistence.queue import InMemoryQueue
from infra_agent.persistence.store import InMemoryStore
from infra_agent.runtime import Platform


@pytest.fixture
def platform(cfg: AgentConfig, monkeypatch: pytest.MonkeyPatch) -> Iterator[Platform]:
    p = Platform(
        config=cfg,
        settings=AppSettings(app_env="test"),
        store=InMemoryStore(),
        queue=InMemoryQueue(),
    )
    monkeypatch.setattr("infra_agent.agentcore_app.get_platform", lambda: p)
    yield p


def test_ping_submit_status(platform: Platform) -> None:
    c = TestClient(app)
    assert c.get("/ping").json() == {"status": "Healthy"}
    r = c.post(
        "/invocations", json={"intent": "list ec2", "account_alias": "dev", "region": "ap-south-1"}
    )
    tid = r.json()["task_id"]
    assert (
        c.post("/invocations", json={"action": "status", "task_id": tid}).json()["status"]
        == "RECEIVED"
    )
    assert (
        c.post(
            "/invocations", json={"intent": "x", "account_alias": "prod", "region": "ap-south-1"}
        ).status_code
        == 422
    )
    assert (
        c.post(
            "/invocations",
            json={"intent": "x", "account_alias": "dev", "region": "ap-south-1", "role_arn": "x"},
        ).status_code
        == 422
    )
