import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from app import agent as assistant
from app.settings import Settings, get_settings
from tests.test_agent_guard import FakeModel

H = {"x-mini-agent": "1"}


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    settings = Settings(database_path=tmp_path / "t.db")
    monkeypatch.setattr("app.api.get_settings", lambda: settings)
    get_settings.cache_clear()
    from app.api import app

    with TestClient(app, base_url="http://127.0.0.1:8000") as c:
        yield c


def test_local_only_and_csrf(client: TestClient) -> None:
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/health", headers={"host": "evil.example"}).status_code == 403
    assert client.post("/api/chats").status_code == 403  # no custom header
    assert client.post("/api/chats", headers=H).status_code == 201


def test_connections_crud(client: TestClient) -> None:
    body = {"kind": "github", "name": "me", "github_user": "adeywm", "max_level": 2}
    created = client.post("/api/connections", json=body, headers=H).json()
    assert client.post("/api/connections", json=body, headers=H).status_code == 400
    assert [c["name"] for c in client.get("/api/connections").json()] == ["me"]
    assert client.delete(f"/api/connections/{created['id']}", headers=H).status_code == 204
    bad = {"kind": "aws", "name": "x"}
    assert client.post("/api/connections", json=bad, headers=H).status_code == 422


def test_model_selection_is_saved_and_used_for_next_chat(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = {"model_id": "in.example.chat-v1", "region": "ap-south-1"}
    assert client.get("/api/model").status_code == 200
    assert client.put("/api/model", json=selected).status_code == 403
    assert client.put("/api/model", json=selected, headers=H).json() == selected
    assert client.get("/api/model").json() == selected
    assert (
        client.put("/api/model", json={**selected, "region": "invalid"}, headers=H).status_code
        == 422
    )
    assert (
        client.put(
            "/api/model", json={**selected, "model_id": "not a model"}, headers=H
        ).status_code
        == 422
    )

    observed: list[tuple[str, str]] = []

    def build(settings: Settings) -> FakeModel:
        observed.append((settings.model_id, settings.model_region))
        return FakeModel(messages=iter([AIMessage(content="Ready")]))

    monkeypatch.setattr(assistant, "build_model", build)
    chat = client.post("/api/chats", headers=H).json()
    with client.stream(
        "POST", f"/api/chats/{chat['id']}/messages", json={"content": "hello"}, headers=H
    ) as response:
        assert response.status_code == 200
        list(response.iter_lines())
    assert observed == [("in.example.chat-v1", "ap-south-1")]


def test_model_catalog_requires_valid_region_and_reports_expired_signin(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from botocore.exceptions import LoginRefreshRequired

    def expired(_settings: Settings, _region: str) -> Any:
        raise LoginRefreshRequired()

    monkeypatch.setattr("app.api.discover_models", expired)
    assert client.get("/api/models/catalog?region=ap-south-1").status_code == 403
    assert client.get("/api/models/catalog?region=invalid", headers=H).status_code == 422
    result = client.get("/api/models/catalog?region=ap-south-1", headers=H)
    assert result.status_code == 401
    assert result.json()["detail"]["code"] == "aws_login_required"
    assert result.json()["detail"]["method"] == "login"


def test_chat_roundtrip(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    model = FakeModel(messages=iter([AIMessage(content="Hello! No connections yet.")]))
    monkeypatch.setattr(assistant, "build_model", lambda _s: model)
    chat = client.post("/api/chats", headers=H).json()
    with client.stream(
        "POST", f"/api/chats/{chat['id']}/messages", json={"content": "hi there"}, headers=H
    ) as resp:
        events: list[dict[str, Any]] = [
            json.loads(line[6:]) for line in resp.iter_lines() if line.startswith("data: ")
        ]
    kinds = [e["type"] for e in events]
    assert kinds[-2:] == ["message", "done"] and set(kinds[:-2]) == {"token"}
    assert (
        "".join(e["text"] for e in events if e["type"] == "token") == "Hello! No connections yet."
    )
    final = events[-2]["message"]
    assert final["content"] == "Hello! No connections yet." and final["tool_calls"] == []
    assert final["id"] == events[0]["id"]  # tokens and the final message share an id
    history = client.get(f"/api/chats/{chat['id']}/messages").json()
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert client.get("/api/chats").json()[0]["title"] == "hi there"
    renamed = client.patch(f"/api/chats/{chat['id']}", json={"title": "Greeting"}, headers=H)
    assert renamed.json()["title"] == "Greeting"
    assert client.delete(f"/api/chats/{chat['id']}", headers=H).status_code == 204
    assert client.get(f"/api/chats/{chat['id']}/messages").status_code == 404


async def test_stopped_tool_calls_are_closed(tmp_path: Path) -> None:
    from langchain_core.messages import HumanMessage
    from langgraph.checkpoint.memory import InMemorySaver

    from app.api import _close_unfinished_tool_calls
    from app.db import Store

    store = Store(tmp_path / "s.db")

    saver = InMemorySaver()
    calls = [{"name": "a", "args": {}, "id": "1"}, {"name": "b", "args": {}, "id": "2"}]
    state = [HumanMessage("hi"), AIMessage(content="", tool_calls=calls)]
    from langgraph.graph import START, MessagesState, StateGraph

    g = StateGraph(MessagesState)
    g.add_node("n", lambda s: {})
    g.add_edge(START, "n")
    graph = g.compile(checkpointer=saver)
    await graph.ainvoke({"messages": state}, {"configurable": {"thread_id": "t"}})
    closing = await _close_unfinished_tool_calls(saver, store, "t")
    assert [m.tool_call_id for m in closing] == ["1", "2"]
    assert await _close_unfinished_tool_calls(saver, store, "missing") == []


def test_model_reasoning_is_separate_and_redacted() -> None:
    from app.api import message_view

    token = "ghp_" + "a" * 36
    message = AIMessage(
        content=[
            {"type": "reasoning_content", "reasoning_content": {"text": "Thinking " + token}},
            {"type": "reasoning_content", "reasoning_content": {"signature": "hidden"}},
            {"type": "text", "text": "Answer"},
        ]
    )
    view = message_view(message)
    assert view is not None and view["content"] == "Answer"
    assert "[REDACTED]" in view["reasoning"] and token not in view["reasoning"]
    assert "hidden" not in view["reasoning"]


def test_tool_call_args_are_redacted() -> None:
    from app.api import message_view

    msg = AIMessage(
        content="", tool_calls=[{"name": "t", "args": {"token": "abcd1234efgh"}, "id": "1"}]
    )
    view = message_view(msg)
    assert view is not None and view["tool_calls"][0]["args"]["token"] == "[REDACTED]"  # noqa: S105 - fake value


def test_expired_connection_and_bedrock_report_actionable_events(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from contextlib import asynccontextmanager

    from botocore.exceptions import LoginRefreshRequired

    @asynccontextmanager
    async def unavailable(*_args: Any) -> Any:
        yield assistant.Toolset(
            problems=[assistant.ToolProblem("personal", "LoginRefreshRequired", "login", None)]
        )

    def expired(_settings: Settings) -> Any:
        raise LoginRefreshRequired()

    monkeypatch.setattr(assistant, "open_toolset", unavailable)
    monkeypatch.setattr(assistant, "build_model", expired)
    chat = client.post("/api/chats", headers=H).json()
    with client.stream(
        "POST", f"/api/chats/{chat['id']}/messages", json={"content": "hello"}, headers=H
    ) as response:
        events = [
            json.loads(line[6:]) for line in response.iter_lines() if line.startswith("data: ")
        ]
    auth = [event for event in events if event["type"] == "auth_required"]
    assert [event["source"] for event in auth] == ["AWS connection personal", "Bedrock"]
    assert all(event["method"] == "login" and event["profile"] is None for event in auth)
    assert not any(event["type"] == "error" for event in events)


def test_unexpected_error_does_not_expose_exception_contents(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def fail(_settings: Settings) -> Any:
        raise RuntimeError("sensitive-example-value")

    monkeypatch.setattr(assistant, "build_model", fail)
    chat = client.post("/api/chats", headers=H).json()
    response = client.post(
        f"/api/chats/{chat['id']}/messages", json={"content": "hello"}, headers=H
    )
    assert "sensitive-example-value" not in response.text
    assert "sensitive-example-value" not in caplog.text
    assert "RuntimeError" in response.text


def test_cost_explorer_validation_error_is_actionable_and_safe(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from botocore.exceptions import ClientError

    def fail(_settings: Settings) -> Any:
        error = ClientError(
            {
                "Error": {
                    "Code": "ValidationException",
                    "Message": (
                        "You haven't enabled historical data beyond 14 months. "
                        "sensitive-example-value"
                    ),
                }
            },
            "GetCostAndUsage",
        )
        raise ExceptionGroup("mcp", [error])

    monkeypatch.setattr(assistant, "build_model", fail)
    chat = client.post("/api/chats", headers=H).json()
    response = client.post(
        f"/api/chats/{chat['id']}/messages", json={"content": "hello"}, headers=H
    )
    assert "GetCostAndUsage" in response.text
    assert "14 months" in response.text
    assert "shorter date range" in response.text
    assert "sensitive-example-value" not in response.text
    assert "sensitive-example-value" not in caplog.text
