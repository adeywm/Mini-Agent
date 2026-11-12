import tempfile
from pathlib import Path
from typing import Any

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import InMemorySaver

from app.agent import Toolset, build_agent
from app.db import Connection, Store


class FakeModel(GenericFakeChatModel):
    def bind_tools(self, tools: Any, **_: Any) -> "FakeModel":
        return self


def _conn(kind: str, name: str, max_level: int) -> Connection:
    extra = {"github_user": "me"} if kind == "github" else {"aws_region": "ap-south-1"}
    return Connection(id=name, created_at="t", kind=kind, name=name, max_level=max_level, **extra)


def _store() -> Store:
    return Store(Path(tempfile.mkdtemp()) / "t.db")


def _call(name: str, args: dict[str, Any], i: int) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"c{i}"}])


async def test_guard_runs_reads_and_refuses_the_rest() -> None:
    ran: list[str] = []

    def tool(name: str, output: str = "ok") -> StructuredTool:
        def fn(**kwargs: Any) -> str:
            ran.append(name)
            return output

        return StructuredTool.from_function(func=fn, name=name, description=name)

    gh, cloud = _conn("github", "me", 1), _conn("aws", "dev", 1)
    toolset = Toolset()
    toolset.add(gh, [tool("list_commits", "token ghp_" + "a" * 36), tool("create_issue")])
    toolset.add(cloud, [tool("aws___run_script"), tool("aws___call_aws")])

    model = FakeModel(
        messages=iter(
            [
                _call("me__list_commits", {}, 1),
                _call("dev__aws___run_script", {"script": "x"}, 3),
                _call("dev__aws___call_aws", {"cli_command": "aws ec2 describe-instances | sh"}, 4),
                _call("ghost__tool", {}, 5),
                AIMessage(content="done"),
            ]
        )
    )
    agent = build_agent(model, toolset, [gh, cloud], InMemorySaver(), _store())
    out = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "go"}]}, {"configurable": {"thread_id": "t"}}
    )
    results = {
        m.tool_call_id: str(m.content) for m in out["messages"] if isinstance(m, ToolMessage)
    }

    assert ran == ["list_commits"]  # nothing else executed
    assert "[REDACTED]" in results["c1"] and "ghp_" not in results["c1"]
    assert results["c3"].startswith("Blocked")
    assert results["c4"].startswith("Blocked")
    assert results["c5"].startswith("Unknown tool")
    assert out["messages"][-1].content == "done"


async def test_cost_validation_error_allows_response_without_repeating_call() -> None:
    from botocore.exceptions import ClientError

    calls = 0

    def aws_read(service: str, operation: str, params: dict[str, Any]) -> str:
        nonlocal calls
        calls += 1
        raise ClientError(
            {
                "Error": {
                    "Code": "ValidationException",
                    "Message": "end date past the beginning of next month sensitive-example-value",
                }
            },
            "GetCostAndUsage",
        )

    cloud = _conn("aws", "dev", 1)
    toolset = Toolset()
    toolset.add(cloud, [StructuredTool.from_function(aws_read, description="Read AWS")])
    params = {
        "TimePeriod": {"Start": "2026-01-01", "End": "2026-12-01"},
        "Granularity": "MONTHLY",
        "Metrics": ["UnblendedCost"],
    }
    args = {"service": "ce", "operation": "GetCostAndUsage", "params": params}
    model = FakeModel(
        messages=iter(
            [
                _call("dev__aws_read", args, 1),
                _call("dev__aws_read", args, 2),
                AIMessage(content="Please choose a valid date range."),
            ]
        )
    )
    agent = build_agent(model, toolset, [cloud], InMemorySaver(), _store())
    out = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "costs"}]},
        {"configurable": {"thread_id": "cost"}},
    )
    results = [str(m.content) for m in out["messages"] if isinstance(m, ToolMessage)]
    assert calls == 1
    assert "end date" in results[0] and "sensitive-example-value" not in results[0]
    assert "already failed" in results[1]
    assert out["messages"][-1].content == "Please choose a valid date range."


def test_large_output_truncated_for_model_but_kept_whole() -> None:
    import json

    from app.agent import MAX_TOOL_OUTPUT, _redact_message
    from app.api import message_view

    big = json.dumps([{"name": f"repo-{i}", "token": "ghp_" + "b" * 36} for i in range(3000)])
    msg = _redact_message(ToolMessage(content=big, tool_call_id="1", name="t"))
    assert len(msg.content) < MAX_TOOL_OUTPUT + 100 and "truncated" in str(msg.content)
    view = message_view(msg)
    assert view is not None
    data = json.loads(view["content"])  # whole and valid for the results panel
    assert len(data) == 3000 and "ghp_" not in view["content"]


async def test_aws_error_goes_back_to_the_model() -> None:
    from botocore.exceptions import ClientError

    def aws_read(service: str, operation: str, params: dict[str, Any]) -> str:
        raise ClientError(
            {"Error": {"Code": "NoSuchBucket"}, "ResponseMetadata": {"HTTPStatusCode": 404}},
            "GetBucketLocation",
        )

    cloud = _conn("aws", "dev", 1)
    toolset = Toolset()
    toolset.add(cloud, [StructuredTool.from_function(aws_read, description="Read AWS")])
    args = {"service": "s3", "operation": "GetBucketLocation", "params": {"Bucket": "x"}}
    model = FakeModel(
        messages=iter([_call("dev__aws_read", args, 1), AIMessage(content="It does not exist.")])
    )
    agent = build_agent(model, toolset, [cloud], InMemorySaver(), _store())
    out = await agent.ainvoke(
        {"messages": [{"role": "user", "content": "where"}]}, {"configurable": {"thread_id": "e"}}
    )
    result = next(m for m in out["messages"] if isinstance(m, ToolMessage))
    assert "GetBucketLocation failed (NoSuchBucket)" in str(result.content)
    assert out["messages"][-1].content == "It does not exist."
