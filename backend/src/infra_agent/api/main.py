"""FastAPI service: task intake, plan/approval review, decisions, audit, webhooks.

Authentication: PostgreSQL-backed browser sessions or configured hashed bearer tokens;
roles operator / approver / auditor / admin. Webhooks authenticate by HMAC signature.
The API never executes AWS/GitHub calls itself: it enqueues work for the worker.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from infra_agent.approval.service import ApprovalError, decide
from infra_agent.auth.sessions import SessionStore
from infra_agent.config import PROJECT_ROOT, ApiUser, AppSettings, ConfigError, load_agent_config
from infra_agent.events.triggers import EventRejected, tasks_for_event, verify_github_signature
from infra_agent.models.domain import TERMINAL_STATUSES, InvalidTransition, Task, TaskStatus
from infra_agent.runtime import Platform

app = FastAPI(title="infra-agent", version="0.2.0")

_dashboard_setting = Path(os.environ.get("INFRA_AGENT_DASHBOARD_DIR", "frontend/dist"))
_DASHBOARD_DIR = (
    _dashboard_setting if _dashboard_setting.is_absolute() else PROJECT_ROOT / _dashboard_setting
)
if _DASHBOARD_DIR.is_dir():
    app.mount("/dashboard", StaticFiles(directory=_DASHBOARD_DIR, html=True), name="dashboard")


@app.middleware("http")
async def security_headers(request: Request, call_next: Any) -> Any:
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    if request.url.path.startswith("/dashboard"):
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; "
            "img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
    return response


@lru_cache(maxsize=1)
def _platform() -> Platform:
    settings = AppSettings()
    return Platform.from_settings(load_agent_config(settings.config_path), settings)


def get_platform() -> Platform:
    return _platform()


PlatformDep = Annotated[Platform, Depends(get_platform)]


def current_user(
    request: Request, platform: PlatformDep, authorization: Annotated[str | None, Header()] = None
) -> ApiUser:
    token = request.cookies.get("infra_session")
    if token and platform.settings.database_url:
        session = SessionStore(platform.settings.database_url).get(token)
        if session:
            if request.method not in ("GET", "HEAD", "OPTIONS") and not hmac.compare_digest(
                request.headers.get("X-CSRF-Token", ""), session.csrf_token
            ):
                raise HTTPException(403, "invalid CSRF token")
            return ApiUser.model_validate(
                {"name": session.username, "roles": session.roles, "token_sha256": "0" * 64}
            )
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    digest = hashlib.sha256(authorization[7:].encode()).hexdigest()
    for user in platform.config.api.users:
        if hmac.compare_digest(user.token_sha256, digest):
            return user
    raise HTTPException(401, "invalid token")


UserDep = Annotated[ApiUser, Depends(current_user)]


def require(user: ApiUser, role: str) -> None:
    if role not in user.roles:
        raise HTTPException(403, f"role {role} required")


class TaskIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: str = Field(min_length=3, max_length=4000)
    account_alias: str
    region: str
    repositories: list[str] = Field(default_factory=list, max_length=20)


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]
    plan_digest: str
    reason: str = Field(default="", max_length=2000)


class CredentialsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=12, max_length=256)


def _session_store(platform: Platform) -> SessionStore:
    if not platform.settings.database_url:
        raise HTTPException(503, "PostgreSQL is required for authentication")
    return SessionStore(platform.settings.database_url)


def _local_setup_request(request: Request) -> None:
    try:
        local = request.client is not None and ipaddress.ip_address(request.client.host).is_loopback
    except ValueError:
        local = False
    origin = request.headers.get("origin", "")
    parsed = urlsplit(origin)
    if (
        not local
        or parsed.scheme != "http"
        or parsed.hostname not in ("localhost", "127.0.0.1", "::1")
        or request.headers.get("X-Requested-With") != "MiniAgent"
    ):
        raise HTTPException(403, "setup is only available from this computer")


@app.get("/auth/setup")
def setup_status(platform: PlatformDep) -> dict[str, bool]:
    return {"setup_required": not _session_store(platform).has_admin()}


@app.post("/auth/setup", status_code=201)
def setup_admin(body: CredentialsIn, request: Request, platform: PlatformDep) -> dict[str, str]:
    _local_setup_request(request)
    if platform.settings.app_env != "local":
        raise HTTPException(403, "local setup is disabled")
    if not _session_store(platform).bootstrap(body.username, body.password):
        raise HTTPException(409, "setup already completed")
    return {"status": "created"}


@app.post("/auth/login")
def login(
    body: CredentialsIn, request: Request, response: Response, platform: PlatformDep
) -> dict[str, Any]:
    if request.headers.get("X-Requested-With") != "MiniAgent":
        raise HTTPException(403, "invalid request")
    if (
        request.url.scheme != "https"
        and not (
            platform.settings.app_env == "local"
            and request.url.hostname in ("localhost", "127.0.0.1", "::1")
        )
        and platform.settings.app_env != "test"
    ):
        raise HTTPException(403, "HTTPS is required")
    result = _session_store(platform).login(body.username, body.password)
    if result is None:
        raise HTTPException(401, "invalid username or password")
    token, session = result
    response.set_cookie(
        "infra_session",
        token,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="strict",
        max_age=28800,
        path="/",
    )
    return {"username": session.username, "roles": session.roles, "csrf_token": session.csrf_token}


@app.get("/auth/me")
def auth_me(request: Request, platform: PlatformDep) -> dict[str, Any]:
    session = _session_store(platform).get(request.cookies.get("infra_session", ""))
    if session is None:
        raise HTTPException(401, "session expired")
    return {"username": session.username, "roles": session.roles, "csrf_token": session.csrf_token}


@app.post("/auth/logout")
def logout(
    request: Request, response: Response, user: UserDep, platform: PlatformDep
) -> dict[str, str]:
    _session_store(platform).logout(request.cookies.get("infra_session", ""))
    response.delete_cookie("infra_session", path="/")
    return {"status": "logged out"}


@app.get("/health/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
def ready(response: Response) -> dict[str, str]:
    try:
        settings = AppSettings()
        load_agent_config(settings.config_path)
    except (ConfigError, ValueError) as exc:
        response.status_code = 503
        return {"status": "not_ready", "reason": type(exc).__name__}
    return {"status": "ready"}


@app.post("/v1/tasks", status_code=202)
def submit_task(body: TaskIn, user: UserDep, platform: PlatformDep) -> dict[str, str]:
    require(user, "operator")
    try:
        account = platform.config.account(body.account_alias)
        if body.region not in account.allowed_regions:
            raise ConfigError(f"region {body.region} not allowed")
        for repo in body.repositories:
            platform.config.repository(repo)
    except ConfigError as exc:
        raise HTTPException(422, str(exc)) from exc
    task = Task(
        requester=user.name,
        origin="api",
        intent=body.intent,
        account_alias=account.alias,
        region=body.region,
        repositories=tuple(body.repositories),
    )
    platform.submit(task)
    return {"task_id": task.id, "status": task.status.value}


def _task_or_404(platform: Platform, task_id: str) -> Task:
    task = platform.store.get_task(task_id)
    if task is None:
        raise HTTPException(404, "task not found")
    return task


@app.get("/v1/tasks")
def list_tasks(
    user: UserDep, platform: PlatformDep, status: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    if status is not None and status not in TaskStatus.__members__:
        raise HTTPException(422, "unknown status")
    return [t.model_dump(mode="json") for t in platform.store.list_tasks(status, min(limit, 200))]


@app.get("/v1/approvals")
def list_approvals(user: UserDep, platform: PlatformDep) -> list[dict[str, Any]]:
    return [a.model_dump(mode="json") for a in platform.store.list_pending_approvals()]


@app.post("/v1/tasks/{task_id}/recover", status_code=202)
def recover_task(task_id: str, user: UserDep, platform: PlatformDep) -> dict[str, str]:
    """Start a new, linked task to assess and repair a failed/partial/uncertain task.

    The recovery plan goes through the same policy and approval path; nothing is rolled
    back automatically.
    """
    require(user, "operator")
    original = _task_or_404(platform, task_id)
    if original.status not in (
        TaskStatus.FAILED,
        TaskStatus.PARTIALLY_COMPLETED,
        TaskStatus.NEEDS_REVIEW,
    ):
        raise HTTPException(409, f"task is {original.status}; nothing to recover")
    history = "\n".join(
        f"- {e.action}: {e.outcome} {e.detail}" for e in platform.store.list_audit(task_id)
    )[-3000:]
    task = Task(
        requester=user.name,
        origin="api",
        account_alias=original.account_alias,
        region=original.region,
        repositories=original.repositories,
        intent=(
            f"Recover task {original.id} ({original.status}). Inspect the actual current "
            f"state first, then propose the minimal safe steps to reach the original goal "
            f"or restore service. Original intent: {original.intent}\n"
            f"<untrusted_execution_history>\n{history}\n</untrusted_execution_history>"
        ),
    )
    platform.submit(task)
    return {"task_id": task.id, "recovers": original.id}


@app.get("/v1/tasks/{task_id}")
def get_task(task_id: str, user: UserDep, platform: PlatformDep) -> dict[str, Any]:
    task = _task_or_404(platform, task_id)
    return task.model_dump(mode="json")


@app.get("/v1/tasks/{task_id}/events")
def task_events(task_id: str, user: UserDep, platform: PlatformDep) -> list[dict[str, Any]]:
    _task_or_404(platform, task_id)
    return [e.model_dump(mode="json") for e in platform.store.list_audit(task_id)]


@app.post("/v1/tasks/{task_id}/cancel")
def cancel_task(task_id: str, user: UserDep, platform: PlatformDep) -> dict[str, str]:
    require(user, "operator")
    task = _task_or_404(platform, task_id)
    if task.status in TERMINAL_STATUSES:
        raise HTTPException(409, f"task already {task.status}")
    try:
        task.transition(TaskStatus.CANCELLED)
    except InvalidTransition as exc:
        raise HTTPException(409, f"cannot cancel while {task.status}; already executing") from exc
    platform.store.save_task(task)
    return {"task_id": task.id, "status": task.status.value}


@app.get("/v1/approvals/{approval_id}")
def get_approval(approval_id: str, user: UserDep, platform: PlatformDep) -> dict[str, Any]:
    approval = platform.store.get_approval(approval_id)
    if approval is None:
        raise HTTPException(404, "approval not found")
    plan = platform.store.get_plan(approval.plan_digest)
    return {
        "approval": approval.model_dump(mode="json"),
        "plan": plan.model_dump(mode="json") if plan else None,
    }


@app.post("/v1/approvals/{approval_id}/decision")
def decide_approval(
    approval_id: str, body: DecisionIn, user: UserDep, platform: PlatformDep
) -> dict[str, str]:
    require(user, "approver")
    approvers = frozenset({user.name})
    try:
        updated = decide(
            platform.store, approval_id, user.name, body.decision, body.plan_digest, approvers
        )
    except ApprovalError as exc:
        raise HTTPException(409, str(exc)) from exc
    platform.queue.enqueue(
        updated.task_id, "resume", {"approval_id": approval_id}, dedupe_key=f"resume:{approval_id}"
    )
    return {"approval_id": approval_id, "decision": updated.decision}


class KillSwitchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frozen: bool


@app.post("/v1/admin/kill-switch")
def kill_switch(body: KillSwitchIn, user: UserDep, platform: PlatformDep) -> dict[str, bool]:
    require(user, "admin")
    platform.store.set_writes_frozen(body.frozen, user.name)
    return {"writes_frozen": platform.store.writes_frozen()}


@app.post("/v1/webhooks/github", status_code=202)
async def github_webhook(
    request: Request,
    platform: PlatformDep,
    x_hub_signature_256: Annotated[str | None, Header()] = None,
    x_github_event: Annotated[str | None, Header()] = None,
    x_github_delivery: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    secret = platform.settings.github_webhook_secret
    if not secret:
        raise HTTPException(503, "webhooks not configured")
    body = await request.body()
    if len(body) > 1_000_000:
        raise HTTPException(413, "payload too large")
    try:
        verify_github_signature(secret, body, x_hub_signature_256)
    except EventRejected as exc:
        raise HTTPException(401, str(exc)) from exc
    if not x_github_delivery or not x_github_event:
        raise HTTPException(400, "missing delivery headers")
    payload = json.loads(body)
    if not platform.store.register_event(
        f"github:{x_github_delivery}", "github", {"event": x_github_event}
    ):
        return {"duplicate": True, "tasks": []}
    try:
        tasks = tasks_for_event(platform.config, "github", x_github_event, payload)
    except ConfigError:
        return {"duplicate": False, "tasks": [], "ignored": "repository not allowlisted"}
    for task in tasks:
        platform.submit(task)
    return {"duplicate": False, "tasks": [t.id for t in tasks]}
