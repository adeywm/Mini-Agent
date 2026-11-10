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
    assert client.delete(f"/api/chats/{chat['id']}", headers=H).status_code == 204
    assert client.get(f"/api/chats/{chat['id']}/messages").status_code == 404


async def test_stopped_tool_calls_are_closed(tmp_path: Path) -> None:
    from langchain_core.messages import HumanMessage
    from langgraph.checkpoint.memory import InMemorySaver

    from app.api import _close_unfinished_tool_calls

    saver = InMemorySaver()
    calls = [{"name": "a", "args": {}, "id": "1"}, {"name": "b", "args": {}, "id": "2"}]
    state = [HumanMessage("hi"), AIMessage(content="", tool_calls=calls)]
    from langgraph.graph import START, MessagesState, StateGraph

    g = StateGraph(MessagesState)
    g.add_node("n", lambda s: {})
    g.add_edge(START, "n")
    graph = g.compile(checkpointer=saver)
    await graph.ainvoke({"messages": state}, {"configurable": {"thread_id": "t"}})
    closing = await _close_unfinished_tool_calls(saver, "t")
    assert [m.tool_call_id for m in closing] == ["1", "2"]
    assert await _close_unfinished_tool_calls(saver, "missing") == []
