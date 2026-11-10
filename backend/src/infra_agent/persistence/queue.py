"""Durable work queue on PostgreSQL (``SELECT ... FOR UPDATE SKIP LOCKED``).

* ``enqueue`` is idempotent per ``dedupe_key`` (webhook delivery ids, approval ids).
* ``claim`` leases one ready job; expired leases are reclaimed (crash recovery).
* ``fail`` retries with backoff until ``max_attempts``, then dead-letters the job.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal, Protocol


@dataclass
class Job:
    id: int
    task_id: str
    kind: Literal["start", "resume"]
    payload: dict[str, Any]
    attempts: int


class WorkQueue(Protocol):
    def enqueue(
        self, task_id: str, kind: str, payload: dict[str, Any], dedupe_key: str | None = None
    ) -> bool: ...
    def claim(self, owner: str, lease_seconds: int = 300) -> Job | None: ...
    def complete(self, job: Job) -> None: ...
    def fail(self, job: Job, error: str) -> None: ...


class InMemoryQueue:
    """Test double."""

    def __init__(self) -> None:
        self.jobs: list[dict[str, Any]] = []
        self.keys: set[str] = set()

    def enqueue(
        self, task_id: str, kind: str, payload: dict[str, Any], dedupe_key: str | None = None
    ) -> bool:
        if dedupe_key is not None:
            if dedupe_key in self.keys:
                return False
            self.keys.add(dedupe_key)
        self.jobs.append(
            {
                "id": len(self.jobs) + 1,
                "task_id": task_id,
                "kind": kind,
                "payload": payload,
                "state": "pending",
                "attempts": 0,
            }
        )
        return True

    def claim(self, owner: str, lease_seconds: int = 300) -> Job | None:
        for j in self.jobs:
            if j["state"] == "pending":
                j["state"] = "leased"
                j["attempts"] += 1
                return Job(j["id"], j["task_id"], j["kind"], j["payload"], j["attempts"])
        return None

    def complete(self, job: Job) -> None:
        self.jobs[job.id - 1]["state"] = "done"

    def fail(self, job: Job, error: str) -> None:
        j = self.jobs[job.id - 1]
        j["state"] = "dead" if j["attempts"] >= 3 else "pending"
        j["error"] = error


class PostgresQueue:
    def __init__(self, dsn: str) -> None:
        import psycopg

        self._dsn = dsn
        self._psycopg = psycopg

    def _conn(self) -> Any:
        return self._psycopg.connect(self._dsn, autocommit=True)

    def enqueue(
        self, task_id: str, kind: str, payload: dict[str, Any], dedupe_key: str | None = None
    ) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                """INSERT INTO work_queue (task_id, kind, payload, dedupe_key)
                   VALUES (%s,%s,%s,%s) ON CONFLICT (dedupe_key) DO NOTHING RETURNING id""",
                (task_id, kind, json.dumps(payload), dedupe_key),
            ).fetchone()
        return row is not None

    def claim(self, owner: str, lease_seconds: int = 300) -> Job | None:
        with self._conn() as conn:
            row = conn.execute(
                """UPDATE work_queue SET state='leased', lease_owner=%s, attempts=attempts+1,
                       lease_until = now() + make_interval(secs => %s)
                   WHERE id = (
                       SELECT id FROM work_queue
                       WHERE (state='pending' AND available_at <= now())
                          OR (state='leased' AND lease_until < now())
                       ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1)
                   RETURNING id, task_id, kind, payload, attempts, max_attempts""",
                (owner, lease_seconds),
            ).fetchone()
            if row is None:
                return None
            if row[4] > row[5]:  # reclaimed too many times -> dead letter
                conn.execute(
                    "UPDATE work_queue SET state='dead', last_error='lease expired too often' "
                    "WHERE id=%s",
                    (row[0],),
                )
                return None
        return Job(row[0], row[1], row[2], row[3], row[4])

    def complete(self, job: Job) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE work_queue SET state='done', lease_until=NULL WHERE id=%s", (job.id,)
            )

    def fail(self, job: Job, error: str) -> None:
        with self._conn() as conn:
            conn.execute(
                """UPDATE work_queue SET last_error=%s, lease_until=NULL,
                       state = CASE WHEN attempts >= max_attempts THEN 'dead' ELSE 'pending' END,
                       available_at = now() + make_interval(secs => 30 * attempts)
                   WHERE id=%s""",
                (error[:2000], job.id),
            )

    def dead_letters(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, task_id, kind, last_error FROM work_queue WHERE state='dead'"
            ).fetchall()
        return [{"id": r[0], "task_id": r[1], "kind": r[2], "error": r[3]} for r in rows]
