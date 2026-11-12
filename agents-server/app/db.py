"""SQLite store for chats and connections (conversation messages live in the LangGraph
checkpointer, in the same file).

A connection stores only *where* credentials come from (a `gh` user name or an AWS profile
name), never the credentials themselves.
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

SCHEMA = """
CREATE TABLE IF NOT EXISTS connections (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL CHECK (kind IN ('aws', 'github')),
    name        TEXT NOT NULL UNIQUE,
    max_level   INTEGER NOT NULL CHECK (max_level BETWEEN 1 AND 3),
    aws_profile TEXT,
    aws_region  TEXT,
    github_user TEXT,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chats (
    id         TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS model_selection (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    model_id    TEXT NOT NULL,
    region      TEXT NOT NULL
);
-- Temporary elevation: the user approved a change above a connection's default level. It
-- lasts for one request (turn_id = the user's message) and is never reused by a later one.
CREATE TABLE IF NOT EXISTS elevations (
    chat_id       TEXT NOT NULL,
    connection_id TEXT NOT NULL,
    turn_id       TEXT NOT NULL,
    level         INTEGER NOT NULL,
    created_at    TEXT NOT NULL,
    PRIMARY KEY (chat_id, connection_id, turn_id)
);
-- One row per approved change (L2/L3 tool call), written before it runs. A row that never
-- reached 'done' or 'error' means the outcome is unknown; that step is never run again.
CREATE TABLE IF NOT EXISTS executions (
    tool_call_id TEXT PRIMARY KEY,
    chat_id      TEXT NOT NULL,
    tool         TEXT NOT NULL,
    args         TEXT NOT NULL,
    level        INTEGER NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('started', 'done', 'error')),
    result       TEXT,
    started_at   TEXT NOT NULL,
    finished_at  TEXT
);
"""

_NAME = re.compile(r"^[a-z][a-z0-9-]{0,30}$")
_REGION = re.compile(r"^[a-z]{2}(-[a-z]+)+-\d$")
_PROFILE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_GH_USER = re.compile(r"^[A-Za-z0-9-]{1,39}$")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class ConnectionIn(BaseModel):
    kind: Literal["aws", "github"]
    name: str = Field(description="short lowercase name, e.g. 'personal' or 'work-aws'")
    max_level: int = Field(default=1, ge=1, le=3)
    aws_profile: str | None = None
    aws_region: str | None = None
    github_user: str | None = None

    @model_validator(mode="after")
    def _check(self) -> ConnectionIn:
        if not _NAME.match(self.name):
            raise ValueError("name: lowercase letters, digits and dashes, starting with a letter")
        if self.kind == "aws":
            if not self.aws_region or not _REGION.match(self.aws_region):
                raise ValueError("aws_region is required, e.g. ap-south-1")
            if self.aws_profile and not _PROFILE.match(self.aws_profile):
                raise ValueError("aws_profile is not a valid profile name")
            self.github_user = None
        else:
            if not self.github_user or not _GH_USER.match(self.github_user):
                raise ValueError("github_user (a `gh auth login` account) is required")
            self.aws_profile = self.aws_region = None
        return self


class Connection(ConnectionIn):
    id: str
    created_at: str

    @property
    def slug(self) -> str:
        """Prefix for this connection's tool names (letters, digits, underscores)."""
        return self.name.replace("-", "_")


class Chat(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str


class ModelSelection(BaseModel):
    model_id: str = Field(min_length=1, max_length=2048, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
    region: str = Field(pattern=r"^[a-z]{2}(-[a-z]+)+-\d$")


class Execution(BaseModel):
    tool_call_id: str
    chat_id: str
    tool: str
    args: str
    level: int
    status: Literal["started", "done", "error"]
    result: str | None = None
    started_at: str
    finished_at: str | None = None


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path
        with self._conn() as db:
            db.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        db = sqlite3.connect(self._path)
        db.row_factory = sqlite3.Row
        return db

    def get_model_selection(self) -> ModelSelection | None:
        with self._conn() as db:
            row = db.execute("SELECT model_id, region FROM model_selection WHERE id=1").fetchone()
        return ModelSelection(**dict(row)) if row else None

    def set_model_selection(self, selection: ModelSelection) -> None:
        with self._conn() as db:
            db.execute(
                "INSERT INTO model_selection (id, model_id, region) VALUES (1,?,?) "
                "ON CONFLICT(id) DO UPDATE SET model_id=excluded.model_id, region=excluded.region",
                (selection.model_id, selection.region),
            )

    # connections -------------------------------------------------------------------
    def add_connection(self, data: ConnectionIn) -> Connection:
        conn = Connection(id=f"con-{uuid.uuid4().hex[:10]}", created_at=_now(), **data.model_dump())
        with self._conn() as db:
            try:
                db.execute(
                    "INSERT INTO connections VALUES (?,?,?,?,?,?,?,?)",
                    (conn.id, conn.kind, conn.name, conn.max_level, conn.aws_profile,
                     conn.aws_region, conn.github_user, conn.created_at),
                )  # fmt: skip
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"a connection named {conn.name!r} already exists") from exc
        return conn

    def list_connections(self) -> list[Connection]:
        with self._conn() as db:
            rows = db.execute("SELECT * FROM connections ORDER BY created_at").fetchall()
        return [Connection(**dict(r)) for r in rows]

    def set_max_level(self, conn_id: str, max_level: int) -> Connection | None:
        with self._conn() as db:
            db.execute("UPDATE connections SET max_level=? WHERE id=?", (max_level, conn_id))
            row = db.execute("SELECT * FROM connections WHERE id=?", (conn_id,)).fetchone()
        return Connection(**dict(row)) if row else None

    def delete_connection(self, conn_id: str) -> bool:
        with self._conn() as db:
            return db.execute("DELETE FROM connections WHERE id=?", (conn_id,)).rowcount > 0

    # chats -------------------------------------------------------------------------
    def create_chat(self, title: str = "New chat") -> Chat:
        now = _now()
        chat = Chat(id=f"chat-{uuid.uuid4().hex[:12]}", title=title, created_at=now, updated_at=now)
        with self._conn() as db:
            db.execute("INSERT INTO chats VALUES (?,?,?,?)", tuple(chat.model_dump().values()))
        return chat

    def get_chat(self, chat_id: str) -> Chat | None:
        with self._conn() as db:
            row = db.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
        return Chat(**dict(row)) if row else None

    def list_chats(self) -> list[Chat]:
        with self._conn() as db:
            rows = db.execute("SELECT * FROM chats ORDER BY updated_at DESC").fetchall()
        return [Chat(**dict(r)) for r in rows]

    def touch_chat(self, chat_id: str, title: str | None = None) -> None:
        with self._conn() as db:
            if title:
                db.execute(
                    "UPDATE chats SET updated_at=?, title=? WHERE id=?", (_now(), title, chat_id)
                )
            else:
                db.execute("UPDATE chats SET updated_at=? WHERE id=?", (_now(), chat_id))

    def delete_chat(self, chat_id: str) -> bool:
        with self._conn() as db:
            return db.execute("DELETE FROM chats WHERE id=?", (chat_id,)).rowcount > 0

    # executions (approved changes) --------------------------------------------------------
    def start_execution(
        self, tool_call_id: str, chat_id: str, tool: str, args: str, level: int
    ) -> Execution | None:
        """Record that an approved change is about to run. Returns the existing record
        instead (and records nothing) if this tool call was already started once."""
        with self._conn() as db:
            row = db.execute(
                "SELECT * FROM executions WHERE tool_call_id=?", (tool_call_id,)
            ).fetchone()
            if row:
                return Execution(**dict(row))
            db.execute(
                "INSERT INTO executions (tool_call_id, chat_id, tool, args, level, status, "
                "started_at) VALUES (?,?,?,?,?,'started',?)",
                (tool_call_id, chat_id, tool, args, level, _now()),
            )
        return None

    def finish_execution(self, tool_call_id: str, status: str, result: str) -> None:
        with self._conn() as db:
            db.execute(
                "UPDATE executions SET status=?, result=?, finished_at=? WHERE tool_call_id=?",
                (status, result, _now(), tool_call_id),
            )

    def elevated_level(self, chat_id: str, connection_id: str, turn_id: str) -> int | None:
        with self._conn() as db:
            row = db.execute(
                "SELECT level FROM elevations WHERE chat_id=? AND connection_id=? AND turn_id=?",
                (chat_id, connection_id, turn_id),
            ).fetchone()
        return int(row["level"]) if row else None

    def elevate(self, chat_id: str, connection_id: str, turn_id: str, level: int) -> None:
        with self._conn() as db:
            db.execute(
                "INSERT INTO elevations VALUES (?,?,?,?,?) ON CONFLICT "
                "(chat_id, connection_id, turn_id) DO UPDATE SET level=max(level, excluded.level)",
                (chat_id, connection_id, turn_id, level, _now()),
            )

    def get_execution(self, tool_call_id: str) -> Execution | None:
        with self._conn() as db:
            row = db.execute(
                "SELECT * FROM executions WHERE tool_call_id=?", (tool_call_id,)
            ).fetchone()
        return Execution(**dict(row)) if row else None

    def raw(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        """Test helper."""
        with self._conn() as db:
            return db.execute(sql, params).fetchall()
