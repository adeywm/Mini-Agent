"""Worker end-to-end on PostgreSQL: submit -> plan -> approval pause -> decision ->
resume -> execute -> verify, with the queue and checkpoints in the database."""

from __future__ import annotations

import os

import psycopg
import pytest

from infra_agent.approval.service import decide
from infra_agent.aws.fixtures import FixtureSession
from infra_agent.aws.reader import AwsReader
from infra_agent.config import AppSettings
from infra_agent.graph.operations import PlanProposal
from infra_agent.models.domain import Task, TaskStatus
from infra_agent.persistence.queue import PostgresQueue
from infra_agent.persistence.store import PostgresStore
from infra_agent.runtime import Platform, run_worker
from tests.integration.test_aws_broker import UPDATE_PARAMS, _StubSessions
from tests.integration.test_operations_graph import READ, WRITE, _config

PG_URL = os.environ.get("INFRA_AGENT_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="INFRA_AGENT_TEST_DATABASE_URL not set")


async def test_worker_full_cycle() -> None:
    store = PostgresStore(PG_URL or "")
    store.migrate()
    with psycopg.connect(PG_URL or "", autocommit=True) as c:
        c.execute("DELETE FROM work_queue")
    cfg = _config()
    sessions = _StubSessions()
    platform = Platform(
        config=cfg,
        settings=AppSettings(database_url=PG_URL, enable_aws_writes=True),
        store=store,
        queue=PostgresQueue(PG_URL or ""),
        aws_session_factory=sessions,
    )
    platform.readers["dev"] = AwsReader(
        FixtureSession(
            {
                "ecs": {
                    "ListServices": {"serviceArns": ["a"]},
                    "DescribeServices": {"services": [{"serviceName": "api", "runningCount": 2}]},
                }
            }
        ),
        cfg.account("dev"),
    )

    async def planner(task: Task) -> PlanProposal:
        return PlanProposal(summary="redeploy", operations=[READ, WRITE])

    platform.planner_override = planner
    task = platform.submit(
        Task(
            requester="bob",
            origin="api",
            intent="redeploy api",
            account_alias="dev",
            region="ap-south-1",
        )
    )
    assert await run_worker(platform, once=True) == 1
    paused = store.get_task(task.id)
    assert paused is not None and paused.status is TaskStatus.AWAITING_APPROVAL

    approval_id = next(
        e.detail.split()[1] for e in store.list_audit(task.id) if e.action == "approval"
    )
    approval = store.get_approval(approval_id)
    assert approval is not None
    decide(store, approval_id, "alice", "approve", approval.plan_digest, frozenset({"alice"}))
    platform.queue.enqueue(
        task.id, "resume", {"approval_id": approval_id}, dedupe_key=f"resume:{approval_id}"
    )

    sessions.stubber.add_response("update_service", {"service": {}}, UPDATE_PARAMS)
    with sessions.stubber:
        assert await run_worker(platform, once=True) == 1
    done = store.get_task(task.id)
    assert done is not None and done.status is TaskStatus.COMPLETED
    actions = [e.action for e in store.list_audit(task.id)]
    assert actions[:3] == ["plan", "policy", "approval"] and actions[-1] == "verify"
    sessions.stubber.assert_no_pending_responses()
