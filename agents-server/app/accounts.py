"""Local sign-in state for connections: which GitHub CLI accounts and AWS profiles exist on
this machine, and whether each saved connection can sign in right now.

Nothing here returns or logs a credential. GitHub is checked through ``gh auth token``
(the token stays in this process); AWS through STS ``GetCallerIdentity``, a read-only call
that needs no IAM permission.
"""

from __future__ import annotations

import re
import shutil
import subprocess  # noqa: S404 - fixed argv, no shell
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

from app.db import Connection
from app.security.aws_errors import expired_login_method
from app.tools.github import github_token

State = Literal["ok", "signed_out", "error"]

_GH_ACCOUNT = re.compile(r"Logged in to github\.com (?:account|as) ([A-Za-z0-9-]+)")


class LocalAccounts(BaseModel):
    gh_installed: bool
    github_users: list[str]
    aws_profiles: list[str]


class ConnectionStatus(BaseModel):
    id: str
    state: State
    detail: str
    # A command the user can run in a terminal to fix it, e.g. "aws sso login --profile dev".
    fix: str | None = None
    # Who the connection signs in as: the GitHub user or the AWS account ID.
    identity: str | None = None


def local_accounts() -> LocalAccounts:
    gh = shutil.which("gh")
    users: list[str] = []
    if gh:
        result = subprocess.run(  # noqa: S603
            [gh, "auth", "status", "--hostname", "github.com"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        # gh prints the status on stderr in older versions and on stdout in newer ones.
        users = list(dict.fromkeys(_GH_ACCOUNT.findall(result.stdout + result.stderr)))
    try:
        import boto3

        profiles = sorted(boto3.Session().available_profiles)
    except Exception:
        profiles = []
    return LocalAccounts(gh_installed=gh is not None, github_users=users, aws_profiles=profiles)


def check(conn: Connection) -> ConnectionStatus:
    return _check_github(conn) if conn.kind == "github" else _check_aws(conn)


def _check_github(conn: Connection) -> ConnectionStatus:
    user = conn.github_user or ""
    if shutil.which("gh") is None:
        return ConnectionStatus(
            id=conn.id,
            state="error",
            detail="GitHub CLI is not installed. Install it from cli.github.com, then sign in.",
            fix="gh auth login",
        )
    try:
        github_token(user)
    except PermissionError:
        return ConnectionStatus(
            id=conn.id,
            state="signed_out",
            detail=f"GitHub CLI has no login for {user}. Sign in as {user}, then check again.",
            fix="gh auth login",
        )
    except subprocess.TimeoutExpired:
        return ConnectionStatus(id=conn.id, state="error", detail="GitHub CLI did not respond.")
    return ConnectionStatus(id=conn.id, state="ok", detail="Signed in", identity=user)


def _check_aws(conn: Connection) -> ConnectionStatus:
    import boto3
    from botocore.config import Config
    from botocore.exceptions import (
        ClientError,
        EndpointConnectionError,
        NoCredentialsError,
        ProfileNotFound,
    )

    profile = conn.aws_profile
    flag = f" --profile {profile}" if profile else ""
    try:
        sts = boto3.Session(profile_name=profile or None).client(
            "sts",
            region_name=conn.aws_region,
            config=Config(connect_timeout=5, read_timeout=5, retries={"total_max_attempts": 1}),
        )
        identity = sts.get_caller_identity()
    except ProfileNotFound:
        return ConnectionStatus(
            id=conn.id,
            state="error",
            detail=f"Profile {profile} is not in your AWS config. Create it, then check again.",
            fix=f"aws configure sso --profile {profile}",
        )
    except NoCredentialsError:
        return ConnectionStatus(
            id=conn.id,
            state="signed_out",
            detail=f"No AWS credentials for profile {profile or 'default'}.",
            fix=f"aws configure{flag}",
        )
    except EndpointConnectionError:
        return ConnectionStatus(
            id=conn.id, state="error", detail=f"Could not reach AWS STS in {conn.aws_region}."
        )
    except Exception as exc:
        method = expired_login_method(exc)
        if method:
            command = "aws sso login" if method == "sso" else "aws login"
            return ConnectionStatus(
                id=conn.id,
                state="signed_out",
                detail="AWS sign-in expired. Sign in again, then check again.",
                fix=command + flag,
            )
        if isinstance(exc, ClientError):
            code = exc.response.get("Error", {}).get("Code", "error")
            if code in ("ExpiredToken", "ExpiredTokenException", "InvalidClientTokenId"):
                return ConnectionStatus(
                    id=conn.id,
                    state="signed_out",
                    detail=f"AWS credentials were rejected ({code}).",
                    fix=f"aws sso login{flag}",
                )
            return ConnectionStatus(id=conn.id, state="error", detail=f"AWS STS failed ({code}).")
        return ConnectionStatus(
            id=conn.id, state="error", detail=f"AWS check failed ({type(exc).__name__})."
        )
    return ConnectionStatus(
        id=conn.id, state="ok", detail="Signed in", identity=str(identity.get("Account", ""))
    )


# Sign-in -----------------------------------------------------------------------------------
# The server runs on the user's own machine, so it can start the same login a terminal would:
# `aws sso login` / `aws login` open the browser themselves; `gh auth login --web` prints a
# one-time code for github.com/login/device. Only these fixed commands run, never a shell.

LOGIN_TIMEOUT = 300  # seconds before an unfinished login is stopped
_URL = re.compile(r"https://[^\s]+")
_CODE = re.compile(r"\b([A-Z0-9]{4}-[A-Z0-9]{4})\b")


class LoginStart(BaseModel):
    command: str  # what is running, e.g. "aws sso login --profile dev"
    url: str | None = None  # page to finish sign-in on, when the CLI printed one
    code: str | None = None  # one-time code to enter on that page (GitHub device flow)


@dataclass
class _Login:
    proc: subprocess.Popen[str]
    command: str
    lines: list[str] = field(default_factory=list)

    def info(self) -> LoginStart:
        text = "\n".join(self.lines)
        url, code = _URL.search(text), _CODE.search(text)
        return LoginStart(
            command=self.command,
            url=url.group(0).rstrip(".") if url else None,
            code=code.group(1) if code else None,
        )


_logins: dict[str, _Login] = {}
_lock = threading.Lock()


def login_argv(conn: Connection, status: ConnectionStatus) -> list[str] | None:
    """The login command for a signed-out connection, or None when it needs a terminal."""
    if status.state == "ok":
        return None
    if conn.kind == "github":
        gh = shutil.which("gh")
        return (
            [gh, "auth", "login", "--hostname", "github.com", "--web", "--git-protocol", "https"]
            if gh
            else None
        )
    fix = status.fix or ""
    aws = shutil.which("aws")
    if (
        status.state != "signed_out"
        or not aws
        or not fix.startswith(("aws sso login", "aws login"))
    ):
        return None
    # The fix is built above from fixed words and a validated profile name.
    return [aws, *fix.split()[1:]]


def start_login(conn: Connection) -> LoginStart:
    """Start (or rejoin) the sign-in for one connection; returns what the user must do next."""
    with _lock:
        running = _logins.get(conn.id)
        if running and running.proc.poll() is None:
            return running.info()
    status = check(conn)
    if status.state == "ok":
        raise ValueError(f"{conn.name} is already signed in")
    argv = login_argv(conn, status)
    if argv is None:
        raise ValueError(f"Sign in from a terminal: {status.fix or status.detail}")
    proc = subprocess.Popen(  # noqa: S603 - fixed argv
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0,
    )
    login = _Login(proc, " ".join(["gh" if conn.kind == "github" else "aws", *argv[1:]]))
    with _lock:
        _logins[conn.id] = login
    try:  # gh waits for Enter before opening the browser
        assert proc.stdin is not None
        proc.stdin.write("\n")
        proc.stdin.close()
    except OSError:
        pass
    threading.Thread(target=_drain, args=(login,), daemon=True).start()
    timer = threading.Timer(LOGIN_TIMEOUT, _stop, args=(proc,))
    timer.daemon = True
    timer.start()
    # Give the CLI a moment to print its URL or code.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and proc.poll() is None:
        info = login.info()
        if info.code or (conn.kind == "aws" and info.url):
            return info
        time.sleep(0.2)
    if proc.poll() not in (None, 0):
        raise ValueError(f"`{login.command}` failed: {(login.lines or ['no output'])[-1][:200]}")
    return login.info()


def _drain(login: _Login) -> None:
    assert login.proc.stdout is not None
    for line in login.proc.stdout:
        if len(login.lines) < 200:
            login.lines.append(line.rstrip())


def _stop(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is None:
        proc.kill()
