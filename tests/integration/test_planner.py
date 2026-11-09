"""Deep Agent planner wiring with a fake tool-calling model (no Bedrock)."""

from __future__ import annotations

from typing import Any

import httpx
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from infra_agent.agents.planner import make_deep_agent_planner, make_github_tools
from infra_agent.config import AgentConfig
from infra_agent.github.client import GitHubReader
from infra_agent.graph.operations import PlanProposal
from infra_agent.models.domain import Task
from infra_agent.persistence.store import InMemoryStore


class _ToolModel(GenericFakeChatModel):
    def bind_tools(self, tools: Any, **_: Any) -> Any:
        return self


async def test_planner_returns_structured_plan_without_executing(cfg: AgentConfig) -> None:
    proposal = {
        "summary": "redeploy api",
        "operations": [
            {
                "service": "ecs",
                "operation": "UpdateService",
                "parameters": {
                    "cluster": "dev-cluster",
                    "service": "api",
                    "forceNewDeployment": True,
                },
                "resource_scope": ["ecs:dev-cluster/api"],
                "expected_outcome": "redeploy",
            }
        ],
    }
    model = _ToolModel(
        messages=iter(
            [
                AIMessage(
                    content="", tool_calls=[{"name": "PlanProposal", "args": proposal, "id": "c1"}]
                ),
            ]
        )
    )
    store = InMemoryStore()
    planner = make_deep_agent_planner(model, aws_tools=[], github_tools=[], store=store)
    task = Task(
        requester="bob",
        origin="api",
        intent="redeploy api",
        account_alias="dev",
        region="ap-south-1",
    )
    result = await planner(task)
    assert isinstance(result, PlanProposal)
    assert result.operations[0].operation == "UpdateService"
    actions = [e.action for e in store.list_audit(task.id)]
    assert "llm" in actions  # every agent step is logged and audited


def test_github_tool_refuses_out_of_scope(cfg: AgentConfig) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={})

    tool = make_github_tools(GitHubReader(cfg, None, httpx.MockTransport(handler)))[0]
    assert tool.invoke({"path": "/repos/attacker/x/contents"}).startswith("refused")
    assert tool.invoke({"path": "/repos/example-org/backend/actions/secrets"}).startswith("refused")
    assert calls == []
