"""End-to-end operations workflow: plan -> policy -> approval (interrupt) -> execute -> verify.

Uses InMemorySaver, and AsyncPostgresSaver when INFRA_AGENT_TEST_DATABASE_URL is set, to
prove a paused task resumes in a *new* graph instance (process restart) exactly once.
"""

from __future__ import annotations

import copy
import os
from typing import Any

import pytest
from botocore.stub import Stubber
from langgraph.checkpoint.memory import InMemorySaver

from infra_agent.approval.service import ApprovalError, decide
from infra_agent.aws.fixtures import FixtureSession
from infra_agent.aws.reader import AwsReader
from infra_agent.config import AgentConfig, AppSettings
from infra_agent.execution.aws_broker import AwsExecutionBroker
from infra_agent.execution.verifier import verify_aws
from infra_agent.graph.operations import (
    OpsDeps,
    PlanProposal,
    ProposedOperation,
    build_operations_graph,
    resume_task,
    start_task,
)
from infra_agent.models.domain import ExecutionStatus, Task, TaskStatus, VerificationSpec
from infra_agent.persistence.store import InMemoryStore, PostgresStore, Store
from tests.conftest import config_dict
from tests.integration.test_aws_broker import UPDATE_PARAMS, _StubSessions

PG_URL = os.environ.get("INFRA_AGENT_TEST_DATABASE_URL")
APPROVERS = frozenset({"alice"})


def _config(rules: list[dict[str, Any]] | None = None) -> AgentConfig:
    data = copy.deepcopy(config_dict())
    data["accounts"][0].update(
        {"permission_level": 3, "writes_enabled": True, "execution_profile": "exec-dev"}
    )
    data["execution"] = {"aws_writes_enabled": True}
    data["policy"] = {"rules": rules or []}
    return AgentConfig.model_validate(data)


VERIFY = VerificationSpec(
    service="ecs",
    operation="DescribeServices",
    params={"cluster": "dev-cluster", "services": ["api"]},
    expect={"services[0].runningCount": 2},
    attempts=1,
    delay_seconds=0,
)


def _proposal(*ops: ProposedOperation) -> PlanProposal:
    return PlanProposal(summary="test", operations=list(ops))


READ = ProposedOperation(
    service="ecs",
    operation="ListServices",
    parameters={"cluster": "dev-cluster"},
    resource_scope=["ecs:dev-cluster"],
    expected_outcome="inventory",
)
WRITE = ProposedOperation(
    service="ecs",
    operation="UpdateService",
    parameters=UPDATE_PARAMS,
    resource_scope=["ecs:dev-cluster/api"],
    depends_on=[0],
    expected_outcome="redeploy",
    verify=VERIFY,
)


class Harness:
    def __init__(
        self,
        cfg: AgentConfig,
        proposal: PlanProposal,
        store: Store | None = None,
        checkpointer: Any = None,
    ) -> None:
        self.cfg = cfg
        self.store = store or InMemoryStore()
        self.sessions = _StubSessions()
        self.broker = AwsExecutionBroker(
            cfg, AppSettings(enable_aws_writes=True), self.store, self.sessions
        )
        self.read_session = FixtureSession(
            {
                "ecs": {
                    "ListServices": {
                        "serviceArns": [
                            "arn:aws:ecs:ap-south-1:111122223333:service/dev-cluster/api"
                        ]
                    },
                    "DescribeServices": {"services": [{"serviceName": "api", "runningCount": 2}]},
                }
            }
        )
        reader = AwsReader(self.read_session, cfg.account("dev"))
        self.proposal = proposal
        self.checkpointer = checkpointer or InMemorySaver()

        async def planner(task: Task) -> PlanProposal:
            return self.proposal

        self.deps = OpsDeps(
            config=cfg,
            store=self.store,
            planner=planner,
            executors={"aws": self.broker.execute},
            verifiers={"aws": lambda op: verify_aws(reader, op, sleep=lambda _: None)},
            readers={"dev": reader},
        )

    def graph(self) -> Any:
        return build_operations_graph(self.deps, self.checkpointer)


def _task(intent: str = "redeploy api") -> Task:
    return Task(
        requester="bob", origin="api", intent=intent, account_alias="dev", region="ap-south-1"
    )


async def test_read_only_plan_runs_autonomously() -> None:
    h = Harness(_config(), _proposal(READ))
    out = await start_task(h.graph(), _task())
    assert out["task"].status is TaskStatus.COMPLETED
    assert h.sessions.calls == 0  # no write session touched
    assert out["records"][0].result["serviceArns"]


async def _approval_flow(h: Harness) -> None:
    task = _task()
    out = await start_task(h.graph(), task)
    assert "__interrupt__" in out
    stored = h.store.get_task(task.id)
    assert stored is not None and stored.status is TaskStatus.AWAITING_APPROVAL
    payload = out["__interrupt__"][0].value
    approval_id, digest = payload["approval_id"], payload["plan_digest"]
    assert h.sessions.calls == 0

    with pytest.raises(ApprovalError, match="own plan"):
        decide(h.store, approval_id, "bob", "approve", digest, frozenset({"bob"}))
    with pytest.raises(ApprovalError, match="digest"):
        decide(h.store, approval_id, "alice", "approve", "sha256:other", APPROVERS)
    decide(h.store, approval_id, "alice", "approve", digest, APPROVERS)

    h.sessions.stubber.add_response("update_service", {"service": {}}, UPDATE_PARAMS)
    with h.sessions.stubber:
        # New graph instance with the same checkpointer = process restart.
        done = await resume_task(h.graph(), task.id, approval_id)
        again = await resume_task(h.graph(), task.id, approval_id)  # replayed resume
    assert done["task"].status is TaskStatus.COMPLETED, done.get("outcome")
    assert "verified" in done["verifications"]["op-002"]
    assert again["task"].status is TaskStatus.COMPLETED
    h.sessions.stubber.assert_no_pending_responses()
    succeeded = [r for r in h.store_executions() if r.status is ExecutionStatus.SUCCEEDED]
    assert len(succeeded) == 1


def _executions(store: Store) -> list[Any]:
    if isinstance(store, InMemoryStore):
        return store.executions
    return []


Harness.store_executions = lambda self: _executions(self.store)  # type: ignore[attr-defined]


async def test_write_pauses_for_approval_and_resumes_once() -> None:
    await _approval_flow(Harness(_config(), _proposal(READ, WRITE)))


@pytest.mark.skipif(not PG_URL, reason="INFRA_AGENT_TEST_DATABASE_URL not set")
async def test_postgres_checkpoint_survives_restart() -> None:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    store = PostgresStore(PG_URL or "")
    store.migrate()
    async with AsyncPostgresSaver.from_conn_string(PG_URL or "") as saver:
        await saver.setup()
        h = Harness(_config(), _proposal(READ, WRITE), store=store, checkpointer=saver)
        task = _task()
        out = await start_task(h.graph(), task)
        payload = out["__interrupt__"][0].value
        decide(store, payload["approval_id"], "alice", "approve", payload["plan_digest"], APPROVERS)
    # Fresh saver connection = restarted process.
    async with AsyncPostgresSaver.from_conn_string(PG_URL or "") as saver2:
        h.checkpointer = saver2
        h.sessions.stubber.add_response("update_service", {"service": {}}, UPDATE_PARAMS)
        with h.sessions.stubber:
            done = await resume_task(h.graph(), task.id, payload["approval_id"])
        assert done["task"].status is TaskStatus.COMPLETED
    loaded = store.get_task(task.id)
    assert loaded is not None and loaded.status is TaskStatus.COMPLETED
    assert [e.action for e in store.list_audit(task.id)][-1] == "verify"


async def test_rejection_and_wrong_resume_never_execute() -> None:
    h = Harness(_config(), _proposal(WRITE.model_copy(update={"depends_on": []})))
    task = _task()
    out = await start_task(h.graph(), task)
    payload = out["__interrupt__"][0].value
    decide(h.store, payload["approval_id"], "alice", "reject", payload["plan_digest"], APPROVERS)
    done = await resume_task(h.graph(), task.id, payload["approval_id"])
    assert done["task"].status is TaskStatus.REJECTED
    assert h.sessions.calls == 0

    h2 = Harness(_config(), _proposal(WRITE.model_copy(update={"depends_on": []})))
    t2 = _task()
    await start_task(h2.graph(), t2)
    done2 = await resume_task(h2.graph(), t2.id, "apr-forged")  # resume value is not authority
    assert done2["task"].status is TaskStatus.REJECTED
    assert h2.sessions.calls == 0


async def test_prohibited_step_denies_whole_plan() -> None:
    bad = ProposedOperation(
        service="cloudtrail",
        operation="StopLogging",
        parameters={"Name": "t"},
        resource_scope=["trail"],
        expected_outcome="x",
    )
    h = Harness(_config([{"id": "all", "effect": "ALLOW_AUTO"}]), _proposal(READ, bad))
    out = await start_task(h.graph(), _task())
    assert out["task"].status is TaskStatus.DENIED
    assert "prohibited" in out["outcome"]
    assert h.read_session.all_calls() == [] and h.sessions.calls == 0


async def test_auto_allowed_write_and_failed_verification_needs_review() -> None:
    bad_verify = WRITE.model_copy(
        update={
            "depends_on": [],
            "verify": VERIFY.model_copy(update={"expect": {"services[0].runningCount": 99}}),
        }
    )
    h = Harness(
        _config([{"id": "dev-ecs", "effect": "ALLOW_AUTO", "service": "ecs"}]),
        _proposal(bad_verify),
    )
    h.sessions.stubber.add_response("update_service", {"service": {}}, UPDATE_PARAMS)
    with h.sessions.stubber:
        out = await start_task(h.graph(), _task())
    assert out["task"].status is TaskStatus.NEEDS_REVIEW
    assert "want 99" in out["verifications"]["op-001"]


async def test_api_failure_after_earlier_write_is_partial() -> None:
    first = WRITE.model_copy(update={"depends_on": []})
    second = WRITE.model_copy(
        update={"depends_on": [0], "parameters": {**UPDATE_PARAMS, "desiredCount": 3}}
    )
    h = Harness(
        _config([{"id": "dev-ecs", "effect": "ALLOW_AUTO", "service": "ecs"}]),
        _proposal(first, second),
    )
    h.sessions.stubber.add_response("update_service", {"service": {}}, UPDATE_PARAMS)
    h.sessions.stubber.add_client_error("update_service", "InvalidParameterException", "bad")
    with h.sessions.stubber:
        out = await start_task(h.graph(), _task())
    assert out["task"].status is TaskStatus.PARTIALLY_COMPLETED
    assert "recovery" in out["outcome"]


async def test_planner_cannot_choose_account_or_bad_dependencies() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ProposedOperation.model_validate({**READ.model_dump(), "account_alias": "prod"})
    h = Harness(_config(), _proposal(READ.model_copy(update={"depends_on": [3]})))
    out = await start_task(h.graph(), _task())
    assert out["task"].status is TaskStatus.FAILED
    assert isinstance(Stubber, type)
