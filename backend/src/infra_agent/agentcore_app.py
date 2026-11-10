"""Amazon Bedrock AgentCore Runtime entrypoint.

AgentCore Runtime runs an HTTP container on port 8080 and calls ``GET /ping`` and
``POST /invocations``; inbound authentication (IAM SigV4 or JWT) is enforced by AgentCore
before requests reach this container. An invocation only *submits* a task to the durable
queue (or reads task status); planning, approval and execution run in the worker, with
state in PostgreSQL outside the ephemeral runtime.

Run locally: ``uv run uvicorn infra_agent.agentcore_app:app --port 8080``.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from infra_agent.api.main import get_platform
from infra_agent.config import ConfigError
from infra_agent.models.domain import Task

app = FastAPI(title="infra-agent-agentcore")


class Invocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["submit", "status"] = "submit"
    intent: str | None = Field(default=None, max_length=4000)
    account_alias: str | None = None
    region: str | None = None
    repositories: list[str] = Field(default_factory=list, max_length=20)
    task_id: str | None = None
    requester: str = Field(default="agentcore", max_length=64)


@app.get("/ping")
def ping() -> dict[str, str]:
    return {"status": "Healthy"}


@app.post("/invocations")
def invocations(body: Invocation) -> dict[str, Any]:
    platform = get_platform()
    if body.action == "status":
        task = platform.store.get_task(body.task_id or "")
        if task is None:
            raise HTTPException(404, "task not found")
        return {"task_id": task.id, "status": task.status.value}
    if not (body.intent and body.account_alias and body.region):
        raise HTTPException(422, "intent, account_alias and region are required")
    try:
        account = platform.config.account(body.account_alias)
        if body.region not in account.allowed_regions:
            raise ConfigError("region not allowed")
        for repo in body.repositories:
            platform.config.repository(repo)
    except ConfigError as exc:
        raise HTTPException(422, str(exc)) from exc
    task = platform.submit(
        Task(
            requester=f"agentcore:{body.requester}",
            origin="api",
            intent=body.intent,
            account_alias=account.alias,
            region=body.region,
            repositories=tuple(body.repositories),
        )
    )
    return {"task_id": task.id, "status": task.status.value}
