from __future__ import annotations

import os

import psycopg
import pytest

from infra_agent.models.domain import new_id
from infra_agent.persistence.queue import PostgresQueue
from infra_agent.persistence.store import PostgresStore

PG_URL = os.environ.get("INFRA_AGENT_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="INFRA_AGENT_TEST_DATABASE_URL not set")


@pytest.fixture
def queue() -> PostgresQueue:
    PostgresStore(PG_URL or "").migrate()
    with psycopg.connect(PG_URL or "", autocommit=True) as c:
        c.execute("DELETE FROM work_queue")
    return PostgresQueue(PG_URL or "")


def test_dedupe_claim_complete(queue: PostgresQueue) -> None:
    key = new_id("delivery")
    assert queue.enqueue("t1", "start", {"a": 1}, key) is True
    assert queue.enqueue("t1", "start", {"a": 1}, key) is False
    job = queue.claim("w1")
    assert job is not None and job.task_id == "t1" and job.payload == {"a": 1}
    assert queue.claim("w2") is None  # leased, not visible
    queue.complete(job)
    assert queue.claim("w2") is None


def test_expired_lease_reclaimed_then_dead_lettered(queue: PostgresQueue) -> None:
    queue.enqueue("t2", "start", {})
    for _ in range(3):
        job = queue.claim("crashy", lease_seconds=0)  # worker "crashes" holding the lease
        assert job is not None
    assert queue.claim("w") is None
    assert [d["task_id"] for d in queue.dead_letters()] == ["t2"]


def test_failures_retry_then_dead(queue: PostgresQueue) -> None:
    queue.enqueue("t3", "start", {})
    job = queue.claim("w")
    assert job is not None
    queue.fail(job, "boom")
    with psycopg.connect(PG_URL or "", autocommit=True) as c:
        c.execute("UPDATE work_queue SET available_at = now()")
    for _ in range(2):
        job = queue.claim("w")
        assert job is not None
        queue.fail(job, "boom")
        with psycopg.connect(PG_URL or "", autocommit=True) as c:
            c.execute("UPDATE work_queue SET available_at = now()")
    assert queue.claim("w") is None
    assert queue.dead_letters()[0]["error"] == "boom"
