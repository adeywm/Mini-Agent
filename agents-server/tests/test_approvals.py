"""Three permission levels with LangChain HITL: L1 runs, L2/L3 pause for approval, the
connection's maximum level is never bypassed, and an approved change runs at most once."""

import json
from collections.abc import Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app import agent as assistant
from app.agent import Toolset, build_agent
from app.db import Store
from app.settings import Settings, get_settings
from tests.test_agent_guard import FakeModel, _call, _conn

H = {"x-mini-agent": "1"}


def _tools(ran: list[str]) -> list[StructuredTool]:
    def make(name: str) -> StructuredTool:
        def fn(**_: Any) -> str:
            ran.append(name)
            return f"{name} ok"

        return StructuredTool.from_function(func=fn, name=name, description=name)

    return [make("list_commits"), make("create_issue"), make("delete_file")]


def _results(out: dict[str, Any]) -> dict[str, str]:
    return {m.tool_call_id: str(m.content) for m in out["messages"] if isinstance(m, ToolMessage)}


CFG = {"configurable": {"thread_id": "t"}}


async def test_change_waits_for_approval_and_runs_once(tmp_path: Path) -> None:
    ran: list[str] = []
    gh, store, saver = _conn("github", "me", 2), Store(tmp_path / "t.db"), InMemorySaver()
    toolset = Toolset()
    toolset.add(gh, _tools(ran))
    calls = [
        {"name": "me__list_commits", "args": {}, "id": "r1"},
        {"name": "me__create_issue", "args": {"title": "Bug"}, "id": "w1"},
    ]
    model = FakeModel(messages=iter([AIMessage(content="", tool_calls=calls), AIMessage("done")]))
    agent = build_agent(model, toolset, [gh], saver, store)

    out = await agent.ainvoke({"messages": [{"role": "user", "content": "go"}]}, CFG)
    assert ran == ["list_commits"]  # L1 ran, the L2 change waits
    (pending,) = out["__interrupt__"]
    about = json.loads(pending.value["description"])
    assert pending.value["name"] == "me__create_issue" and about["level"] == 2
    assert about["connection"] == "me" and store.get_execution("w1") is None

    out = await agent.ainvoke(Command(resume={pending.id: {"type": "approve"}}), CFG)
    assert ran == ["list_commits", "create_issue"]
    assert _results(out)["w1"] == "create_issue ok" and out["messages"][-1].content == "done"
    record = store.get_execution("w1")
    assert record is not None and record.status == "done" and record.chat_id == "t"


async def test_rejected_change_never_runs(tmp_path: Path) -> None:
    ran: list[str] = []
    gh = _conn("github", "me", 3)
    toolset = Toolset()
    toolset.add(gh, _tools(ran))
    model = FakeModel(
        messages=iter([_call("me__delete_file", {"path": "a"}, 1), AIMessage("kept it")])
    )
    agent = build_agent(model, toolset, [gh], InMemorySaver(), Store(tmp_path / "t.db"))
    out = await agent.ainvoke({"messages": [{"role": "user", "content": "rm"}]}, CFG)
    (pending,) = out["__interrupt__"]
    assert json.loads(pending.value["description"])["level"] == 3
    out = await agent.ainvoke(Command(resume={pending.id: {"type": "reject"}}), CFG)
    assert ran == [] and "rejected" in _results(out)["c1"]
    assert out["messages"][-1].content == "kept it"


async def test_lowered_level_is_enforced_when_resuming(tmp_path: Path) -> None:
    ran: list[str] = []
    saver, store = InMemorySaver(), Store(tmp_path / "t.db")
    first = Toolset()
    first.add(_conn("github", "me", 2), _tools(ran))
    model = FakeModel(messages=iter([_call("me__create_issue", {}, 1)]))
    out = await build_agent(model, first, [], saver, store).ainvoke(
        {"messages": [{"role": "user", "content": "go"}]}, CFG
    )
    (pending,) = out["__interrupt__"]

    lowered = Toolset()  # the user changed the connection to L1 before approving
    lowered.add(_conn("github", "me", 1), _tools(ran))
    model = FakeModel(messages=iter([AIMessage("ok")]))
    agent = build_agent(model, lowered, [], saver, store)
    out = await agent.ainvoke(Command(resume={pending.id: {"type": "approve"}}), CFG)
    assert ran == [] and "above the maximum level" in _results(out)["c1"]


async def test_started_change_is_never_run_again(tmp_path: Path) -> None:
    ran: list[str] = []
    gh, store = _conn("github", "me", 2), Store(tmp_path / "t.db")
    toolset = Toolset()
    toolset.add(gh, _tools(ran))
    model = FakeModel(messages=iter([_call("me__create_issue", {}, 1), AIMessage("ok")]))
    agent = build_agent(model, toolset, [gh], InMemorySaver(), store)
    out = await agent.ainvoke({"messages": [{"role": "user", "content": "go"}]}, CFG)
    # An earlier attempt started (e.g. the server crashed mid-call): never repeat it.
    store.start_execution("c1", "t", "me__create_issue", "{}", 2)
    out = await agent.ainvoke(
        Command(resume={out["__interrupt__"][0].id: {"type": "approve"}}), CFG
    )
    assert ran == [] and "outcome" in _results(out)["c1"]


async def test_iam_is_level_three(tmp_path: Path) -> None:
    ran: list[str] = []

    def call_aws(cli_command: str) -> str:
        ran.append(cli_command)
        return "[]"

    tool = StructuredTool.from_function(call_aws, name="aws___call_aws", description="aws")
    args = {"cli_command": "aws iam list-users"}
    for level, interrupted in ((2, False), (3, True)):
        cloud = _conn("aws", "dev", level)
        toolset = Toolset()
        toolset.add(cloud, [tool])
        model = FakeModel(messages=iter([_call("dev__aws___call_aws", args, 1), AIMessage("x")]))
        agent = build_agent(
            model, toolset, [cloud], InMemorySaver(), Store(tmp_path / f"{level}.db")
        )
        out = await agent.ainvoke({"messages": [{"role": "user", "content": "users"}]}, CFG)
        assert bool(out.get("__interrupt__")) is interrupted
        if not interrupted:
            assert "above the maximum level" in _results(out)["c1"]
    assert ran == []


# API: approval cards, digest binding, restart, and moving on without approving ---------
@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    value = Settings(database_path=tmp_path / "t.db")
    monkeypatch.setattr("app.api.get_settings", lambda: value)
    get_settings.cache_clear()
    return value


def _client() -> Iterator[TestClient]:
    from app.api import app

    with TestClient(app, base_url="http://127.0.0.1:8000") as c:
        yield c


def _events(client: TestClient, path: str, body: dict[str, Any]) -> list[dict[str, Any]]:
    with client.stream("POST", path, json=body, headers=H) as resp:
        assert resp.status_code == 200, resp.read()
        return [json.loads(line[6:]) for line in resp.iter_lines() if line.startswith("data: ")]


def _fake_world(monkeypatch: pytest.MonkeyPatch, ran: list[str], replies: list[AIMessage]) -> None:
    @asynccontextmanager
    async def toolset(connections: list[Any], _settings: Any) -> Any:
        found = Toolset()
        for conn in connections:
            found.add(conn, _tools(ran))
        yield found

    model = FakeModel(disable_streaming=True, messages=iter(replies))
    monkeypatch.setattr(assistant, "open_toolset", toolset)
    monkeypatch.setattr(assistant, "build_model", lambda _s: model)


def test_api_approval_survives_restart_and_binds_to_the_call(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran: list[str] = []
    _fake_world(
        monkeypatch,
        ran,
        [_call("me__create_issue", {"title": "Bug"}, 1), AIMessage("Created the issue.")],
    )
    client_ctx = _client()
    client = next(client_ctx)
    client.post(
        "/api/connections",
        headers=H,
        json={"kind": "github", "name": "me", "github_user": "me", "max_level": 2},
    )
    chat = client.post("/api/chats", headers=H).json()["id"]
    events = _events(client, f"/api/chats/{chat}/messages", {"content": "file a bug"})
    (card,) = [e["approval"] for e in events if e["type"] == "approval"]
    assert card["level"] == 2 and card["name"] == "me__create_issue" and card["approvable"]
    assert card["args"] == {"title": "Bug"} and ran == []
    next(client_ctx, None)  # stop the server

    client_ctx = _client()  # restart: the approval is still waiting
    client = next(client_ctx)
    assert [a["id"] for a in client.get(f"/api/chats/{chat}/approvals").json()] == [card["id"]]
    url = f"/api/chats/{chat}/approvals/{card['id']}"
    wrong = {"decision": "approve", "digest": "sha256:" + "0" * 64}
    assert client.post(url, json=wrong, headers=H).status_code == 409
    events = _events(client, url, {"decision": "approve", "digest": card["digest"]})
    assert ran == ["create_issue"]
    assert any(e.get("message", {}).get("content") == "Created the issue." for e in events)
    assert client.get(f"/api/chats/{chat}/approvals").json() == []
    replay = {"decision": "approve", "digest": card["digest"]}
    assert client.post(url, json=replay, headers=H).status_code == 409  # not run twice
    assert ran == ["create_issue"]
    next(client_ctx, None)


def test_api_new_message_dismisses_pending_change(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran: list[str] = []
    _fake_world(
        monkeypatch,
        ran,
        [_call("me__delete_file", {"path": "a"}, 1), AIMessage("Okay, leaving it.")],
    )
    client_ctx = _client()
    client = next(client_ctx)
    client.post(
        "/api/connections",
        headers=H,
        json={"kind": "github", "name": "me", "github_user": "me", "max_level": 3},
    )
    chat = client.post("/api/chats", headers=H).json()["id"]
    _events(client, f"/api/chats/{chat}/messages", {"content": "delete a"})
    assert len(client.get(f"/api/chats/{chat}/approvals").json()) == 1
    _events(client, f"/api/chats/{chat}/messages", {"content": "never mind"})
    history = client.get(f"/api/chats/{chat}/messages").json()
    result = next(m for m in history if m["role"] == "tool")
    assert result["content"].startswith("Not run") and ran == []
    assert client.get(f"/api/chats/{chat}/approvals").json() == []
    assert history[-1]["content"] == "Okay, leaving it."
    next(client_ctx, None)


async def test_invalid_aws_parameters_never_reach_the_approval_card(tmp_path: Path) -> None:
    ran: list[dict[str, Any]] = []

    def aws_critical(**kwargs: Any) -> str:
        ran.append(kwargs)
        return "{}"

    tool = StructuredTool.from_function(aws_critical, name="aws_critical", description="L3")
    cloud = _conn("aws", "dev", 3)
    toolset = Toolset()
    toolset.add(cloud, [tool])
    bad = {"service": "opensearchserverless", "operation": "DeleteCollection", "params": {}}
    model = FakeModel(messages=iter([_call("dev__aws_critical", bad, 1), AIMessage("fixing")]))
    agent = build_agent(model, toolset, [cloud], InMemorySaver(), Store(tmp_path / "t.db"))
    out = await agent.ainvoke({"messages": [{"role": "user", "content": "delete"}]}, CFG)
    assert not out.get("__interrupt__") and ran == []
    assert 'Missing required parameter in input: "id"' in _results(out)["c1"]
