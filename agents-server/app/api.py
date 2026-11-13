"""HTTP API for the chat UI.

Local, single user: the server binds to 127.0.0.1 and acts with your accounts, so it rejects
requests for other host names (DNS rebinding) and requires a custom header on every change
(a cross-site page cannot send it without a CORS preflight, which is never allowed).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    ToolMessage,
)
from langgraph.types import Command, Interrupt
from pydantic import BaseModel, Field

from app import agent as assistant
from app.accounts import (
    ConnectionStatus,
    LocalAccounts,
    LoginStart,
    check,
    check_aws,
    local_accounts,
    start_aws_login,
    start_login,
)
from app.bedrock_models import discover_models
from app.db import Chat, Connection, ConnectionIn, ModelSelection, Store
from app.security.aws_errors import (
    expired_login_method,
    login_error_type,
    root_cause,
    service_error,
    tool_login_context,
)
from app.security.redaction import redact, redact_text
from app.settings import get_settings

log = logging.getLogger("mini_agent.api")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]"}
CSRF_HEADER = "x-mini-agent"
# Shown for tool calls cut off by Stop. The UI keys on this prefix; the outcome is unknown,
# so a stopped change must never be treated as "not done".
STOPPED_TEXT = "Stopped by the user; the outcome of this step is unknown."


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


# Bedrock model preference and on-demand discovery -------------------------------------
@app.get("/api/model")
def get_model(request: Request) -> ModelSelection:
    settings = get_settings()
    return _store(request).get_model_selection() or ModelSelection(
        model_id=settings.model_id, region=settings.model_region
    )


@app.put("/api/model")
def select_model(selection: ModelSelection, request: Request) -> ModelSelection:
    _store(request).set_model_selection(selection)
    return selection


@app.get("/api/models/catalog")
def model_catalog(
    request: Request, region: str = Query(pattern=r"^[a-z]{2}(-[a-z]+)+-\d$")
) -> dict[str, Any]:
    # Discovery contacts AWS; require the same anti-CSRF header as state-changing requests.
    if request.headers.get(CSRF_HEADER) != "1":
        raise HTTPException(403, f"missing {CSRF_HEADER} header")
    settings = get_settings()
    try:
        return discover_models(settings, region)
    except Exception as exc:
        method = expired_login_method(exc)
        if method:
            log.warning("Bedrock model discovery needs AWS sign-in (%s)", type(exc).__name__)
            raise HTTPException(
                401,
                detail={
                    "code": "aws_login_required",
                    "method": method,
                    "profile": settings.bedrock_profile,
                },
            ) from None
        log.warning("Bedrock model discovery failed (%s)", type(exc).__name__)
        raise HTTPException(
            503, "Could not load Bedrock models. Check your profile and region."
        ) from None


# connections -------------------------------------------------------------------------
@app.get("/api/connections")
def list_connections(request: Request) -> list[Connection]:
    return _store(request).list_connections()


@app.get("/api/connections/status")
async def connection_status(request: Request) -> list[ConnectionStatus]:
    # Contacts gh and AWS STS; require the anti-CSRF header like other outward calls.
    if request.headers.get(CSRF_HEADER) != "1":
        raise HTTPException(403, f"missing {CSRF_HEADER} header")
    connections = _store(request).list_connections()
    return list(await asyncio.gather(*(asyncio.to_thread(check, c) for c in connections)))


@app.post("/api/connections/{conn_id}/login")
async def login_connection(conn_id: str, request: Request) -> LoginStart:
    """Start the CLI sign-in for a signed-out connection; the dialog then polls the status."""
    conn = next((c for c in _store(request).list_connections() if c.id == conn_id), None)
    if conn is None:
        raise HTTPException(404, "connection not found")
    try:
        return await asyncio.to_thread(start_login, conn)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


class AwsProfile(BaseModel):
    profile: str | None = Field(default=None, pattern=r"^[A-Za-z0-9._-]{1,64}$")


@app.post("/api/aws-login")
async def aws_login(body: AwsProfile) -> LoginStart:
    """Sign in an AWS profile from the chat, e.g. the one Bedrock uses (None = default)."""
    try:
        return await asyncio.to_thread(start_aws_login, body.profile)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None


@app.post("/api/aws-login/status")
async def aws_login_status(body: AwsProfile) -> ConnectionStatus:
    return await asyncio.to_thread(check_aws, "aws", body.profile, None)


@app.get("/api/local-accounts")
async def list_local_accounts(request: Request) -> LocalAccounts:
    if request.headers.get(CSRF_HEADER) != "1":
        raise HTTPException(403, f"missing {CSRF_HEADER} header")
    return await asyncio.to_thread(local_accounts)


@app.post("/api/connections", status_code=201)
def add_connection(body: ConnectionIn, request: Request) -> Connection:
    try:
        return _store(request).add_connection(body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.delete("/api/connections/{conn_id}", status_code=204)
async def delete_connection(conn_id: str, request: Request) -> None:
    # Safe while a change awaits approval: the guard re-checks connections when it resumes.
    if not _store(request).delete_connection(conn_id):
        raise HTTPException(404, "connection not found")


class ConnectionLevel(BaseModel):
    max_level: Literal[1, 2, 3]


@app.patch("/api/connections/{conn_id}")
async def update_connection_level(
    conn_id: str, body: ConnectionLevel, request: Request
) -> Connection:
    # A lowered level also applies to changes already awaiting approval (checked on resume).
    connection = _store(request).set_max_level(conn_id, body.max_level)
    if connection is None:
        raise HTTPException(404, "connection not found")
    return connection


# chats ---------------------------------------------------------------------------------
@app.get("/api/chats")
def list_chats(request: Request) -> list[Chat]:
    return _store(request).list_chats()


@app.post("/api/chats", status_code=201)
def create_chat(request: Request) -> Chat:
    return _store(request).create_chat()


class ChatPatch(BaseModel):
    title: str = Field(min_length=1, max_length=120)


@app.patch("/api/chats/{chat_id}")
def rename_chat(chat_id: str, body: ChatPatch, request: Request) -> Chat:
    store = _store(request)
    if store.get_chat(chat_id) is None:
        raise HTTPException(404, "chat not found")
    store.touch_chat(chat_id, title=body.title.strip())
    return store.get_chat(chat_id)  # type: ignore[return-value]


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


def _reasoning(content: Any) -> str:
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "reasoning_content":
            continue
        reasoning = block.get("reasoning_content")
        if isinstance(reasoning, dict) and isinstance(reasoning.get("text"), str):
            parts.append(reasoning["text"])
    return redact_text("".join(parts))


def message_view(msg: BaseMessage) -> dict[str, Any] | None:
    if isinstance(msg, HumanMessage):
        return {"role": "user", "content": _text(msg.content)}
    if isinstance(msg, AIMessage):
        return {
            "role": "assistant",
            "id": msg.id,
            "content": _text(msg.content),
            "reasoning": _reasoning(msg.content),
            "tool_calls": [
                {"id": c["id"], "name": c["name"], "args": redact(c["args"])}
                for c in msg.tool_calls
            ],
        }
    if isinstance(msg, ToolMessage):
        return {
            "role": "tool",
            "tool_call_id": msg.tool_call_id,
            "name": msg.name,
            "status": msg.status,
            # The full output (artifact) when the model only saw a truncated copy.
            "content": msg.artifact["full"]
            if isinstance(msg.artifact, dict) and isinstance(msg.artifact.get("full"), str)
            else _text(msg.content),
        }
    return None


# Conversation state ------------------------------------------------------------------
# A paused run keeps finished tool results and approval requests as checkpoint "pending
# writes" (one task per tool call) until every call in that step is answered.
class Snapshot(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    messages: list[BaseMessage] = []
    finished: list[ToolMessage] = []  # results of a paused step that already finished
    pending: list[Interrupt] = []  # approval requests not answered yet
    resumed: set[str] = set()  # tool call ids approved/answered but with no result yet


async def _snapshot(saver: Any, chat_id: str) -> Snapshot:
    saved = await saver.aget_tuple({"configurable": {"thread_id": chat_id}})
    if not saved:
        return Snapshot()
    writes = saved.pending_writes or []
    by_task: dict[str, dict[str, list[Any]]] = {}
    for task_id, channel, value in writes:
        values = value if isinstance(value, list | tuple) else [value]
        by_task.setdefault(task_id, {}).setdefault(channel, []).extend(values)
    snap = Snapshot(messages=list(saved.checkpoint["channel_values"].get("messages", [])))
    for channels in by_task.values():
        results = [m for m in channels.get("messages", []) if isinstance(m, ToolMessage)]
        snap.finished.extend(results)
        interrupts = [i for i in channels.get("__interrupt__", []) if isinstance(i, Interrupt)]
        if results or not interrupts:
            continue
        if "__resume__" in channels:  # answered, then cut off before a result was saved
            snap.resumed.update(str(i.value.get("tool_call_id")) for i in interrupts)
        else:
            snap.pending.extend(interrupts)
    return snap


def _digest(name: str, args: Any) -> str:
    raw = json.dumps([name, args], sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()


def approval_view(item: Interrupt) -> dict[str, Any]:
    value = item.value if isinstance(item.value, dict) else {}
    args = value.get("args") or {}
    try:
        about = json.loads(value.get("description") or "{}")
    except ValueError:
        about = {}
    shown = redact(args)
    return {
        "id": item.id,
        "tool_call_id": value.get("tool_call_id"),
        "name": value.get("name"),
        "args": shown,
        "digest": _digest(str(value.get("name")), args),
        # Never approve something the user cannot fully see.
        "approvable": shown == args,
        **{
            k: about.get(k)
            for k in (
                "level",
                "reason",
                "connection",
                "kind",
                "account",
                "default_level",
                "elevates_to",
            )
        },  # fmt: skip
        "warnings": about.get("warnings") or [],
    }


@app.get("/api/chats/{chat_id}/messages")
async def get_messages(chat_id: str, request: Request) -> list[dict[str, Any]]:
    if _store(request).get_chat(chat_id) is None:
        raise HTTPException(404, "chat not found")
    snap = await _snapshot(request.app.state.saver, chat_id)
    return [v for m in [*snap.messages, *snap.finished] if (v := message_view(m))]


@app.get("/api/chats/{chat_id}/approvals")
async def get_approvals(chat_id: str, request: Request) -> list[dict[str, Any]]:
    if _store(request).get_chat(chat_id) is None:
        raise HTTPException(404, "chat not found")
    snap = await _snapshot(request.app.state.saver, chat_id)
    return [approval_view(i) for i in snap.pending]


DISMISSED_TEXT = (
    "Not run: the user sent a new message instead of approving this change. Do not retry it "
    "unless the user asks again."
)


async def _close_unfinished_tool_calls(saver: Any, store: Store, chat_id: str) -> list[BaseMessage]:
    """Answer tool calls left open by Stop or an unanswered approval, so the history stays
    valid (the model API rejects tool calls without results).

    Results that finished inside a paused step are carried over. A call still waiting for
    approval certainly did not run; anything else that started has an unknown outcome.
    """
    snap = await _snapshot(saver, chat_id)
    messages = snap.messages
    last_ai = next(
        (i for i in range(len(messages) - 1, -1, -1) if isinstance(messages[i], AIMessage)), None
    )
    if last_ai is None:
        return []
    answered = {m.tool_call_id for m in messages[last_ai:] if isinstance(m, ToolMessage)}
    carried = [m for m in snap.finished if m.tool_call_id not in answered]
    answered |= {m.tool_call_id for m in carried}
    waiting = {str(i.value.get("tool_call_id")) for i in snap.pending}
    closing: list[BaseMessage] = list(carried)
    for c in messages[last_ai].tool_calls:
        if c["id"] in answered:
            continue
        not_run = c["id"] in waiting and store.get_execution(c["id"]) is None
        closing.append(
            ToolMessage(
                content=DISMISSED_TEXT if not_run else STOPPED_TEXT,
                tool_call_id=c["id"],
                name=c["name"],
                status="error",
            )
        )
    return closing


class MessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


def _settings(store: Store) -> Any:
    settings = get_settings()
    selected_model = store.get_model_selection()
    if selected_model is not None:
        settings = settings.model_copy(
            update={"model_id": selected_model.model_id, "model_region": selected_model.region}
        )
    return settings


def _chat_lock(request: Request, chat_id: str) -> asyncio.Lock:
    lock: asyncio.Lock = request.app.state.locks.setdefault(chat_id, asyncio.Lock())
    if lock.locked():
        raise HTTPException(409, "this chat is still working on the previous message")
    return lock


GraphInput = Callable[[], Awaitable[Any]]


def _run(request: Request, chat_id: str, lock: asyncio.Lock, make_input: GraphInput) -> Any:
    """Stream one agent run (a new message or a resumed approval) as server-sent events.

    Every run builds the toolset from the connections as they are now, so the guard enforces
    the current maximum levels even for a change approved long after it was proposed.
    """
    store = _store(request)
    settings = _settings(store)
    connections = store.list_connections()
    saver = request.app.state.saver

    async def run() -> AsyncIterator[str]:
        async with lock:
            stage = "connecting tools"
            try:
                async with assistant.open_toolset(connections, settings) as toolset:
                    for problem in toolset.problems:
                        if problem.login_method:
                            yield _sse(
                                {
                                    "type": "auth_required",
                                    "source": f"AWS connection {problem.connection}",
                                    "profile": problem.profile,
                                    "method": problem.login_method,
                                    "error_type": problem.error_type,
                                }
                            )
                        else:
                            yield _sse(
                                {"type": "notice", "content": f"Unavailable: {problem.summary}"}
                            )
                    stage = "starting Bedrock model"
                    graph = assistant.build_agent(
                        assistant.build_model(settings), toolset, connections, saver, store
                    )
                    config = {
                        "configurable": {"thread_id": chat_id},
                        "recursion_limit": settings.max_agent_steps * 2 + 1,
                    }
                    stream = graph.astream(
                        await make_input(), config, stream_mode=["messages", "updates"]
                    )
                    stage = "generating response"
                    async for mode, data in stream:
                        if mode == "messages":  # live tokens from the model
                            chunk, meta = data
                            if (
                                not isinstance(chunk, AIMessageChunk)
                                or meta.get("langgraph_node") != "model"
                            ):
                                continue
                            # Model reasoning streams first (models that expose it), then text.
                            if thought := _reasoning(chunk.content):
                                yield _sse({"type": "reasoning", "id": chunk.id, "text": thought})
                            if text := _text(chunk.content):
                                yield _sse({"type": "token", "id": chunk.id, "text": text})
                            continue
                        for key, node in data.items():  # complete messages
                            if key.startswith("__") or not isinstance(node, dict):
                                continue  # "__interrupt__": approvals are sent below
                            for msg in node.get("messages", []):
                                if view := message_view(msg):
                                    yield _sse({"type": "message", "message": view})
                    for item in (await _snapshot(saver, chat_id)).pending:
                        yield _sse({"type": "approval", "approval": approval_view(item)})
            except Exception as exc:
                method = expired_login_method(exc)
                if method:
                    tool_context = tool_login_context(exc)
                    error_type = login_error_type(exc)
                    log.warning(
                        "chat %s: AWS sign-in expired while %s (%s)",
                        chat_id,
                        stage,
                        error_type,
                    )
                    yield _sse(
                        {
                            "type": "auth_required",
                            "source": f"AWS connection {tool_context.connection}"
                            if tool_context
                            else "Bedrock",
                            "profile": tool_context.profile
                            if tool_context
                            else settings.bedrock_profile,
                            "method": method,
                            "error_type": error_type,
                        }
                    )
                elif (failure := service_error(exc)) is not None:
                    log.warning(
                        "chat %s: AWS %s failed (%s)", chat_id, failure.operation, failure.code
                    )
                    message = f"AWS {failure.operation} failed ({failure.code})."
                    if failure.operation in ("Converse", "ConverseStream"):
                        message += (
                            f" Bedrock rejected the request for model {settings.model_id} "
                            f"in {settings.model_region}. Check the model ID, region, "
                            "model access and whether the model supports chat and streaming. "
                            "You can choose another model under Settings > Model."
                        )
                    if failure.cost_history_unavailable:
                        message += (
                            " Cost Explorer reported that historical data beyond 14 months "
                            "is not enabled. Choose a shorter date range or review "
                            "Cost Explorer settings."
                        )
                    yield _sse({"type": "error", "content": message})
                else:
                    # Name the real error, not the exception-group wrapper; redact its text.
                    cause = root_cause(exc)
                    detail = redact_text(str(cause))[:300]
                    log.error(
                        "chat %s failed while %s: %s: %s",
                        chat_id,
                        stage,
                        type(cause).__name__,
                        detail,
                    )
                    yield _sse(
                        {
                            "type": "error",
                            "content": f"Chat failed while {stage}: {type(cause).__name__}"
                            + (f": {detail}" if detail else ""),
                        }
                    )
            finally:
                store.touch_chat(chat_id)
                yield _sse({"type": "done"})

    return StreamingResponse(run(), media_type="text/event-stream")


@app.post("/api/chats/{chat_id}/messages")
async def send_message(chat_id: str, body: MessageIn, request: Request) -> StreamingResponse:
    store = _store(request)
    chat = store.get_chat(chat_id)
    if chat is None:
        raise HTTPException(404, "chat not found")
    lock = _chat_lock(request, chat_id)
    if chat.title == "New chat":
        store.touch_chat(chat_id, title=body.content.strip().splitlines()[0][:60])
    saver = request.app.state.saver

    async def make_input() -> Any:
        inputs = await _close_unfinished_tool_calls(saver, store, chat_id)
        inputs.append(HumanMessage(content=body.content))
        return {"messages": inputs}

    return _run(request, chat_id, lock, make_input)


class Decision(BaseModel):
    decision: Literal["approve", "reject"]
    digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    message: str | None = Field(default=None, max_length=2000)


@app.post("/api/chats/{chat_id}/approvals/{interrupt_id}")
async def decide(
    chat_id: str, interrupt_id: str, body: Decision, request: Request
) -> StreamingResponse:
    """Answer one approval card and resume the paused run (LangGraph ``Command(resume=)``).

    The decision binds to the exact call: the digest of its tool name and arguments must
    match the call that is waiting, or nothing happens.
    """
    if _store(request).get_chat(chat_id) is None:
        raise HTTPException(404, "chat not found")
    lock = _chat_lock(request, chat_id)
    snap = await _snapshot(request.app.state.saver, chat_id)
    item = next((i for i in snap.pending if i.id == interrupt_id), None)
    if item is None:
        raise HTTPException(409, "this change is no longer waiting for approval")
    view = approval_view(item)
    if view["digest"] != body.digest:
        raise HTTPException(409, "the change differs from the one you reviewed")
    if body.decision == "approve" and not view["approvable"]:
        raise HTTPException(409, "this change contains hidden values and cannot be approved")
    answer: dict[str, Any] = {"type": body.decision}
    if body.decision == "reject" and body.message:
        answer["message"] = body.message

    async def make_input() -> Any:
        return Command(resume={interrupt_id: answer})

    return _run(request, chat_id, lock, make_input)
