"""FastAPI service: task intake, plan/approval review, decisions, audit, webhooks.

Authentication: bearer tokens mapped to configured users by SHA-256 (constant-time
compare); roles operator / approver / auditor. Webhooks authenticate by HMAC signature.
The API never executes AWS/GitHub calls itself: it enqueues work for the worker.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from functools import lru_cache
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from infra_agent.approval.service import ApprovalError, decide
from infra_agent.config import ApiUser, AppSettings, ConfigError, load_agent_config
from infra_agent.events.triggers import EventRejected, tasks_for_event, verify_github_signature
from infra_agent.models.domain import TERMINAL_STATUSES, InvalidTransition, Task, TaskStatus
from infra_agent.runtime import Platform

app = FastAPI(title="infra-agent", version="0.2.0")


@lru_cache(maxsize=1)
def _platform() -> Platform:
    settings = AppSettings()
    return Platform.from_settings(load_agent_config(settings.config_path), settings)


def get_platform() -> Platform:
    return _platform()


PlatformDep = Annotated[Platform, Depends(get_platform)]


def current_user(
    platform: PlatformDep, authorization: Annotated[str | None, Header()] = None
) -> ApiUser:
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
    approvers = frozenset(u.name for u in platform.config.api.users if "approver" in u.roles)
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
