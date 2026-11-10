"""HTTP API for the chat UI.

Local, single user: the server binds to 127.0.0.1 and acts with your accounts, so it rejects
requests for other host names (DNS rebinding) and requires a custom header on every change
(a cross-site page cannot send it without a CORS preflight, which is never allowed).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)
from pydantic import BaseModel, Field

from app import agent as assistant
from app.db import Chat, Connection, ConnectionIn, Store
from app.settings import get_settings

log = logging.getLogger("mini_agent.api")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]"}
CSRF_HEADER = "x-mini-agent"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    settings = get_settings()
    app.state.store = Store(settings.db_file)
    app.state.locks = {}
    async with AsyncSqliteSaver.from_conn_string(str(settings.db_file)) as saver:
        app.state.saver = saver
        yield


app = FastAPI(title="Mini-Agent", lifespan=lifespan)


@app.middleware("http")
async def local_only(request: Request, call_next: Any) -> Any:
    host = (request.headers.get("host") or "").rsplit(":", 1)[0]
    if host not in LOCAL_HOSTS:
        return JSONResponse({"detail": "localhost only"}, status_code=403)
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get(CSRF_HEADER) != "1":
        return JSONResponse({"detail": f"missing {CSRF_HEADER} header"}, status_code=403)
    return await call_next(request)


def _store(request: Request) -> Store:
    return request.app.state.store  # type: ignore[no-any-return]


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# connections -------------------------------------------------------------------------
@app.get("/api/connections")
def list_connections(request: Request) -> list[Connection]:
    return _store(request).list_connections()


@app.post("/api/connections", status_code=201)
def add_connection(body: ConnectionIn, request: Request) -> Connection:
    try:
        return _store(request).add_connection(body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.delete("/api/connections/{conn_id}", status_code=204)
def delete_connection(conn_id: str, request: Request) -> None:
    if not _store(request).delete_connection(conn_id):
        raise HTTPException(404, "connection not found")


# chats ---------------------------------------------------------------------------------
@app.get("/api/chats")
def list_chats(request: Request) -> list[Chat]:
    return _store(request).list_chats()


@app.post("/api/chats", status_code=201)
def create_chat(request: Request) -> Chat:
    return _store(request).create_chat()


@app.delete("/api/chats/{chat_id}", status_code=204)
async def delete_chat(chat_id: str, request: Request) -> None:
    if not _store(request).delete_chat(chat_id):
        raise HTTPException(404, "chat not found")
    await request.app.state.saver.adelete_thread(chat_id)


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        )
    return str(content)


def message_view(msg: BaseMessage) -> dict[str, Any] | None:
    if isinstance(msg, HumanMessage):
        return {"role": "user", "content": _text(msg.content)}
    if isinstance(msg, AIMessage):
        return {
            "role": "assistant",
            "id": msg.id,
            "content": _text(msg.content),
            "tool_calls": [
                {"id": c["id"], "name": c["name"], "args": c["args"]} for c in msg.tool_calls
            ],
        }
    if isinstance(msg, ToolMessage):
        return {
            "role": "tool",
            "tool_call_id": msg.tool_call_id,
            "name": msg.name,
            "status": msg.status,
            "content": _text(msg.content)[:20_000],
        }
    return None


@app.get("/api/chats/{chat_id}/messages")
async def get_messages(chat_id: str, request: Request) -> list[dict[str, Any]]:
    if _store(request).get_chat(chat_id) is None:
        raise HTTPException(404, "chat not found")
    saved = await request.app.state.saver.aget_tuple({"configurable": {"thread_id": chat_id}})
    messages = saved.checkpoint["channel_values"].get("messages", []) if saved else []
    return [v for m in messages if (v := message_view(m))]


async def _close_unfinished_tool_calls(saver: Any, chat_id: str) -> list[BaseMessage]:
    """Answer tool calls left open by a stopped run, so the history stays valid.

    If the user pressed Stop after the model asked for tools, the saved history ends with
    tool calls that have no result; the model API rejects such a history.
    """
    saved = await saver.aget_tuple({"configurable": {"thread_id": chat_id}})
    messages = saved.checkpoint["channel_values"].get("messages", []) if saved else []
    last_ai = next(
        (i for i in range(len(messages) - 1, -1, -1) if isinstance(messages[i], AIMessage)), None
    )
    if last_ai is None:
        return []
    answered = {m.tool_call_id for m in messages[last_ai:] if isinstance(m, ToolMessage)}
    return [
        ToolMessage(
            content="Stopped by the user before this finished; the result is unknown.",
            tool_call_id=c["id"],
            name=c["name"],
            status="error",
        )
        for c in messages[last_ai].tool_calls
        if c["id"] not in answered
    ]


class MessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


@app.post("/api/chats/{chat_id}/messages")
async def send_message(chat_id: str, body: MessageIn, request: Request) -> StreamingResponse:
    store = _store(request)
    chat = store.get_chat(chat_id)
    if chat is None:
        raise HTTPException(404, "chat not found")
    lock: asyncio.Lock = request.app.state.locks.setdefault(chat_id, asyncio.Lock())
    if lock.locked():
        raise HTTPException(409, "this chat is still working on the previous message")
    if chat.title == "New chat":
        store.touch_chat(chat_id, title=body.content.strip().splitlines()[0][:60])
    settings = get_settings()
    connections = store.list_connections()
    saver = request.app.state.saver

    async def run() -> AsyncIterator[str]:
        async with lock:
            try:
                async with assistant.open_toolset(connections, settings) as toolset:
                    for problem in toolset.problems:
                        yield _sse({"type": "notice", "content": f"Unavailable: {problem}"})
                    graph = assistant.build_agent(
                        assistant.build_model(settings), toolset, connections, saver
                    )
                    config = {
                        "configurable": {"thread_id": chat_id},
                        "recursion_limit": settings.max_agent_steps * 2 + 1,
                    }
                    inputs = await _close_unfinished_tool_calls(saver, chat_id)
                    inputs.append(HumanMessage(content=body.content))
                    stream = graph.astream(
                        {"messages": inputs}, config, stream_mode=["messages", "updates"]
                    )
                    async for mode, data in stream:
                        if mode == "messages":  # live tokens from the model
                            chunk, meta = data
                            if (
                                isinstance(chunk, AIMessageChunk)
                                and meta.get("langgraph_node") == "model"
                                and (text := _text(chunk.content))
                            ):
                                yield _sse({"type": "token", "id": chunk.id, "text": text})
                            continue
                        for node in data.values():  # complete messages
                            for msg in (node or {}).get("messages", []):
                                if view := message_view(msg):
                                    yield _sse({"type": "message", "message": view})
            except Exception as exc:
                log.exception("chat %s failed", chat_id)
                yield _sse({"type": "error", "content": f"{type(exc).__name__}: {exc}"[:500]})
            finally:
                store.touch_chat(chat_id)
                yield _sse({"type": "done"})

    return StreamingResponse(run(), media_type="text/event-stream")
