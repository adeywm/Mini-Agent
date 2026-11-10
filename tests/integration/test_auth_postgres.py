"""Browser authentication against an explicitly configured test PostgreSQL database."""

from __future__ import annotations

import os

import psycopg
import pytest
from fastapi.testclient import TestClient

from infra_agent.api.main import app, get_platform
from infra_agent.config import AgentConfig, AppSettings
from infra_agent.persistence.queue import PostgresQueue
from infra_agent.persistence.store import PostgresStore
from infra_agent.runtime import Platform
from tests.conftest import config_dict

PG_URL = os.environ.get("INFRA_AGENT_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="INFRA_AGENT_TEST_DATABASE_URL not set")


def test_browser_session_persists_and_enforces_csrf() -> None:
    assert PG_URL
    store = PostgresStore(PG_URL)
    store.migrate()
    platform = Platform(
        config=AgentConfig.model_validate(config_dict()),
        settings=AppSettings(app_env="local", database_url=PG_URL),
        store=store,
        queue=PostgresQueue(PG_URL),
    )
    app.dependency_overrides[get_platform] = lambda: platform
    try:
        client = TestClient(app, base_url="http://localhost:8080", client=("127.0.0.1", 12345))
        headers = {"Origin": "http://localhost:5173", "X-Requested-With": "MiniAgent"}
        credentials = {"username": "test-admin", "password": "local-test-password"}
        assert client.get("/auth/setup").json() == {"setup_required": True}
        assert client.get("/auth/me").status_code == 401
        assert client.post("/auth/setup", json=credentials, headers=headers).status_code == 201
        assert client.post("/auth/setup", json=credentials, headers=headers).status_code == 409
        assert (
            client.post(
                "/auth/login",
                json={**credentials, "password": "invalid-password"},
                headers=headers,
            ).status_code
            == 401
        )
        response = client.post("/auth/login", json=credentials, headers=headers)
        assert response.status_code == 200
        assert "HttpOnly" in response.headers["set-cookie"]
        csrf = response.json()["csrf_token"]
        assert client.get("/auth/me").json()["username"] == "test-admin"
        task = {"intent": "inspect resources", "account_alias": "dev", "region": "ap-south-1"}
        assert client.post("/v1/tasks", json=task).status_code == 403
        submitted = client.post("/v1/tasks", json=task, headers={"X-CSRF-Token": csrf})
        assert submitted.status_code == 202
        assert client.post("/auth/logout", headers={"X-CSRF-Token": csrf}).status_code == 200
        assert client.get("/auth/me").status_code == 401
        assert client.post("/auth/login", json=credentials, headers=headers).status_code == 200
        with psycopg.connect(PG_URL) as conn:
            conn.execute("UPDATE app_sessions SET expires_at = now() - interval '1 second'")
        assert client.get("/auth/me").status_code == 401
    finally:
        app.dependency_overrides.clear()
