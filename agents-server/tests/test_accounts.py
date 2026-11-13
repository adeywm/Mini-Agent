"""Sign-in checks for connections: every case offline, no gh or AWS needed."""

import subprocess
from typing import Any

import pytest
from botocore.exceptions import NoCredentialsError, ProfileNotFound, SSOTokenLoadError
from fastapi.testclient import TestClient

from app import accounts
from app.db import Connection
from tests.test_api import H, client  # noqa: F401 - fixture

GH = Connection(id="g", kind="github", name="me", github_user="octo", created_at="")
AWS = Connection(
    id="a", kind="aws", name="dev", aws_profile="dev", aws_region="ap-south-1", created_at=""
)


def test_github_states(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(accounts.shutil, "which", lambda _: None)
    assert accounts.check(GH).state == "error"

    monkeypatch.setattr(accounts.shutil, "which", lambda _: "gh")

    def signed_out(_: str) -> str:
        raise PermissionError("no login")

    monkeypatch.setattr(accounts, "github_token", signed_out)
    status = accounts.check(GH)
    assert (status.state, status.fix) == ("signed_out", "gh auth login")

    monkeypatch.setattr(accounts, "github_token", lambda _: "secret-token")
    status = accounts.check(GH)
    assert (status.state, status.identity) == ("ok", "octo")
    assert "secret-token" not in status.model_dump_json()


class _Session:
    error: Exception | None = None

    def __init__(self, profile_name: str | None = None) -> None:
        if isinstance(self.error, ProfileNotFound):
            raise self.error

    def client(self, *_: Any, **__: Any) -> "_Session":
        return self

    def get_caller_identity(self) -> dict[str, str]:
        if self.error:
            raise self.error
        return {"Account": "123456789012", "Arn": "arn:aws:sts::123456789012:assumed-role/x"}


@pytest.mark.parametrize(
    ("error", "state", "fix"),
    [
        (None, "ok", None),
        (SSOTokenLoadError(error_msg="expired"), "signed_out", "aws sso login --profile dev"),
        (NoCredentialsError(), "signed_out", "aws configure --profile dev"),
        (ProfileNotFound(profile="dev"), "error", "aws configure sso --profile dev"),
    ],
)
def test_aws_states(
    monkeypatch: pytest.MonkeyPatch, error: Exception | None, state: str, fix: str | None
) -> None:
    monkeypatch.setattr(_Session, "error", error)
    monkeypatch.setattr("boto3.Session", _Session)
    status = accounts.check(AWS)
    assert (status.state, status.fix) == (state, fix)
    if state == "ok":
        assert status.identity == "123456789012"


def test_local_accounts_parses_gh_status(monkeypatch: pytest.MonkeyPatch) -> None:
    out = (
        "github.com\n  ✓ Logged in to github.com account octo (keyring)\n"
        "  ✓ Logged in to github.com account work-bot (keyring)\n"
        "  X Failed to log in to github.com account gone (keyring)\n"
    )
    monkeypatch.setattr(accounts.shutil, "which", lambda _: "gh")
    monkeypatch.setattr(
        accounts.subprocess,
        "run",
        lambda *_, **__: subprocess.CompletedProcess([], 0, stdout="", stderr=out),
    )
    assert accounts.local_accounts().github_users == ["octo", "work-bot"]


def test_status_endpoint_needs_csrf_header(
    client: TestClient,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = {"kind": "github", "name": "me", "github_user": "octo"}
    client.post("/api/connections", json=body, headers=H)
    monkeypatch.setattr(
        "app.api.check", lambda c: accounts.ConnectionStatus(id=c.id, state="ok", detail="")
    )
    assert client.get("/api/connections/status").status_code == 403
    assert client.get("/api/local-accounts").status_code == 403
    assert [s["state"] for s in client.get("/api/connections/status", headers=H).json()] == ["ok"]


def test_login_argv_only_runs_fixed_login_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(accounts.shutil, "which", lambda name: f"/bin/{name}")

    def status(state: str, fix: str | None) -> accounts.ConnectionStatus:
        return accounts.ConnectionStatus(id="x", state=state, detail="", fix=fix)  # type: ignore[arg-type]

    assert accounts.login_argv(GH, status("signed_out", "gh auth login"))[1:4] == [  # type: ignore[index]
        "auth",
        "login",
        "--hostname",
    ]
    assert accounts.login_argv(AWS, status("signed_out", "aws sso login --profile dev")) == [
        "/bin/aws",
        "sso",
        "login",
        "--profile",
        "dev",
    ]
    # Interactive setup, unknown errors and signed-in connections stay in the terminal.
    assert accounts.login_argv(AWS, status("signed_out", "aws configure --profile dev")) is None
    assert accounts.login_argv(AWS, status("error", "aws configure sso --profile dev")) is None
    assert accounts.login_argv(GH, status("ok", None)) is None


class _Proc:
    def __init__(self, argv: list[str], **_: Any) -> None:
        import io

        self.argv = argv
        self.stdin = io.StringIO()
        self.stdout = io.StringIO(
            "! First copy your one-time code: AB12-CD34\n"
            "Open this URL to continue in your web browser: https://github.com/login/device\n"
        )

    def poll(self) -> None:
        return None

    def kill(self) -> None:
        pass


def test_start_login_returns_the_device_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(accounts.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(
        accounts,
        "check",
        lambda c: accounts.ConnectionStatus(
            id=c.id, state="signed_out", detail="", fix="gh auth login"
        ),
    )
    monkeypatch.setattr(accounts.subprocess, "Popen", _Proc)
    accounts._logins.clear()
    info = accounts.start_login(GH)
    assert (info.code, info.url) == ("AB12-CD34", "https://github.com/login/device")
    assert accounts.start_login(GH) == info  # a second click rejoins the running login
    accounts._logins.clear()
