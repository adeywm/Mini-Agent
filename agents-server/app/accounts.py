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
