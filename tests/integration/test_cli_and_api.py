from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from infra_agent.api.main import app
from infra_agent.cli import main
from tests.conftest import EXAMPLE_CONFIG, FIXTURES

CFG = ["--config", str(EXAMPLE_CONFIG)]


def test_cli_investigate_mock(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "report.md"
    rc = main(
        [
            *CFG,
            "investigate",
            "--account",
            "dev",
            "--region",
            "ap-south-1",
            "--cluster",
            "dev-cluster",
            "--mock",
            "--github-fixture",
            "--repo",
            "example-org/backend",
            "--no-llm",
            "--out",
            str(out),
        ]
    )
    assert rc == 0
    text = out.read_text(encoding="utf-8")
    assert "Mode: **mock**" in text and "IMAGE_PULL_FAILURE" in text


def test_cli_read_fixture_and_refuse_write(capsys: pytest.CaptureFixture[str]) -> None:
    base = [
        *CFG,
        "read",
        "--account",
        "dev",
        "--region",
        "ap-south-1",
        "--fixture",
        str(FIXTURES / "ecs_unhealthy.json"),
        "--service",
        "ecs",
    ]
    assert (
        main([*base, "--operation", "ListServices", "--params", '{"cluster": "dev-cluster"}']) == 0
    )
    assert json.loads(capsys.readouterr().out)["data"]["serviceArns"]
    assert main([*base, "--operation", "DeleteService", "--params", '{"service": "api"}']) == 1
    assert "only READ is executed" in capsys.readouterr().err


def test_cli_live_requires_opt_in(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(
        [
            *CFG,
            "investigate",
            "--account",
            "dev",
            "--region",
            "ap-south-1",
            "--cluster",
            "dev-cluster",
            "--no-llm",
        ]
    )
    assert rc == 1
    assert "live reads are disabled" in capsys.readouterr().err


def test_cli_discover_offline(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["discover", "--service", "ecs"]) == 0
    out = capsys.readouterr().out
    assert "DescribeServices" in out and "DeleteService" not in out


def test_api_health(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(app)
    assert client.get("/health/live").json() == {"status": "ok"}
    monkeypatch.setenv("INFRA_AGENT_CONFIG_PATH", "does/not/exist.toml")
    r = client.get("/health/ready")
    assert r.status_code == 503 and r.json()["reason"] == "ConfigError"
    monkeypatch.setenv("INFRA_AGENT_CONFIG_PATH", str(EXAMPLE_CONFIG))
    assert client.get("/health/ready").json() == {"status": "ready"}
