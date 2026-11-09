from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from infra_agent.aws.fixtures import FixtureSession
from infra_agent.config import AgentConfig, load_agent_config
from infra_agent.integrations.github_source import FixtureGitHubSource

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "src" / "infra_agent" / "fixtures"
EXAMPLE_CONFIG = ROOT / "config" / "agent.example.toml"


@pytest.fixture(autouse=True)
def _no_ambient_aws(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests never use real AWS credentials or env-driven settings."""
    for var in ("AWS_PROFILE", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-south-1")
    for var in (
        "INFRA_AGENT_LIVE_READS",
        "INFRA_AGENT_ENABLE_AWS_WRITES",
        "INFRA_AGENT_ENABLE_SANDBOX_WRITES",
        "INFRA_AGENT_CONFIG_PATH",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def cfg() -> AgentConfig:
    return load_agent_config(EXAMPLE_CONFIG)


@pytest.fixture
def fixture_session() -> FixtureSession:
    return FixtureSession.from_file(FIXTURES / "ecs_unhealthy.json")


@pytest.fixture
def github_source() -> FixtureGitHubSource:
    return FixtureGitHubSource.from_file(FIXTURES / "github_runs.json")


def config_dict() -> dict[str, Any]:
    import tomllib

    return tomllib.loads(EXAMPLE_CONFIG.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def event_loop_policy() -> Any:
    """psycopg async cannot use Windows' default ProactorEventLoop."""
    import asyncio
    import sys

    if sys.platform == "win32":
        return asyncio.WindowsSelectorEventLoopPolicy()  # type: ignore[attr-defined]
    return asyncio.DefaultEventLoopPolicy()
