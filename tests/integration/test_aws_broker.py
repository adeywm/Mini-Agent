"""Execution broker gates, against a real botocore client stubbed with botocore.Stubber.

No network is used: Stubber intercepts calls; any unexpected call fails the test.
"""

from __future__ import annotations

import copy
from datetime import timedelta
from typing import Any

import boto3
import pytest
from botocore.exceptions import EndpointConnectionError
from botocore.stub import Stubber

from infra_agent.aws.fixtures import FixtureSession
from infra_agent.aws.reader import AwsReader
from infra_agent.config import AgentConfig, AppSettings
from infra_agent.execution.aws_broker import AwsExecutionBroker
from infra_agent.execution.verifier import get_path, verify_aws
from infra_agent.models.domain import (
    ApprovalRequest,
    ChangePlan,
    ExecutionStatus,
    PlannedOperation,
    VerificationSpec,
    utcnow,
)
from infra_agent.persistence.store import InMemoryStore
from tests.conftest import config_dict

UPDATE_PARAMS = {"cluster": "dev-cluster", "service": "api", "forceNewDeployment": True}


def _config(rules: list[dict[str, Any]] | None = None, writes: bool = True) -> AgentConfig:
    data = copy.deepcopy(config_dict())
    data["accounts"][0].update(
        {
            "permission_level": 3,
            "writes_enabled": writes,
            "execution_profile": "infra-agent-exec-dev",
        }
    )
    data["execution"] = {"aws_writes_enabled": writes}
    data["policy"] = {"rules": rules or []}
    return AgentConfig.model_validate(data)


def _op(
    service: str = "ecs", operation: str = "UpdateService", params: dict[str, Any] | None = None
) -> PlannedOperation:
    return PlannedOperation(
        operation_id="op-1",
        provider="aws",
        account_alias="dev",
        environment="development",
        service=service,
        operation=operation,
        region="ap-south-1",
        resource_scope=("ecs:dev-cluster/api",),
        parameters=params or UPDATE_PARAMS,
        expected_outcome="new deployment",
        verification="steady",
        idempotency_key="k1",
    )


def _plan(op: PlannedOperation) -> ChangePlan:
    return ChangePlan(id="plan-1", version=1, task_id="task-1", operations=(op,))


class _StubSessions:
    def __init__(self) -> None:
        self.client = boto3.Session(region_name="ap-south-1").client(
            "ecs", region_name="ap-south-1"
        )
        self.stubber = Stubber(self.client)
        self.calls = 0

    def __call__(self, account: Any) -> Any:
        outer = self

        class _S:
            def client(self, service: str, **_: Any) -> Any:
                outer.calls += 1
                return outer.client

        return _S()


def _broker(
    cfg: AgentConfig, env_writes: bool = True
) -> tuple[AwsExecutionBroker, InMemoryStore, _StubSessions]:
    store = InMemoryStore()
    sessions = _StubSessions()
    settings = AppSettings(enable_aws_writes=env_writes)
    return AwsExecutionBroker(cfg, settings, store, sessions), store, sessions


@pytest.mark.parametrize(("config_writes", "env_writes"), [(False, True), (True, False)])
def test_any_switch_off_blocks(config_writes: bool, env_writes: bool) -> None:
    cfg = _config([{"id": "auto", "effect": "ALLOW_AUTO"}], writes=config_writes)
    broker, _, sessions = _broker(cfg, env_writes)
    op = _op()
    rec = broker.execute(_plan(op), op)
    assert rec.status is ExecutionStatus.BLOCKED and "writes disabled" in rec.reason
    assert sessions.calls == 0


def test_auto_allowed_write_executes_once() -> None:
    broker, store, sessions = _broker(
        _config([{"id": "dev-ecs", "effect": "ALLOW_AUTO", "service": "ecs"}])
    )
    sessions.stubber.add_response(
        "update_service",
        {"service": {"serviceName": "api"}, "ResponseMetadata": {"RequestId": "req-1"}},
        UPDATE_PARAMS,
    )
    op = _op()
    plan = _plan(op)
    with sessions.stubber:
        first = broker.execute(plan, op)
        second = broker.execute(plan, op)  # replay -> same record, no second API call
    assert first.status is ExecutionStatus.SUCCEEDED
    assert first.external_request_id == "req-1"
    assert second.id == first.id
    sessions.stubber.assert_no_pending_responses()
    statuses = [r.status for r in store.executions]
    assert statuses == [ExecutionStatus.STARTED, ExecutionStatus.SUCCEEDED]
    assert store.audit[-1].outcome == "allowed"


def _approval(plan: ChangePlan, **over: Any) -> ApprovalRequest:
    base: dict[str, Any] = {
        "id": "apr-1",
        "plan_digest": plan.digest,
        "task_id": plan.task_id,
        "scope": ("op-1",),
        "requester": "agent",
        "approver": "alice",
        "decision": "approved",
        "expires_at": utcnow() + timedelta(minutes=30),
    }
    base.update(over)
    return ApprovalRequest.model_validate(base)


def test_approval_required_and_bound_to_plan() -> None:
    broker, store, sessions = _broker(_config())  # default -> REQUIRE_APPROVAL
    op = _op()
    plan = _plan(op)
    assert broker.execute(plan, op).status is ExecutionStatus.BLOCKED  # none supplied

    store.save_approval(_approval(plan))
    rec = broker.execute(plan, op, "apr-1")  # approved but not consumed by workflow
    assert rec.status is ExecutionStatus.BLOCKED and "consumed" in rec.reason

    store.save_approval(_approval(plan, id="apr-2", scope=("op-other",)))
    store.consume_approval("apr-2", plan.digest)
    assert "scope" in broker.execute(plan, op, "apr-2").reason

    other = _plan(_op(params={**UPDATE_PARAMS, "desiredCount": 50}))
    store.save_approval(_approval(other, id="apr-3"))
    store.consume_approval("apr-3", other.digest)
    assert "does not match" in broker.execute(plan, op, "apr-3").reason
    assert sessions.calls == 0

    store.save_approval(_approval(plan, id="apr-4"))
    store.consume_approval("apr-4", plan.digest)
    sessions.stubber.add_response("update_service", {"service": {}}, UPDATE_PARAMS)
    with sessions.stubber:
        assert broker.execute(plan, op, "apr-4").status is ExecutionStatus.SUCCEEDED


def test_prohibited_and_tampered_blocked() -> None:
    broker, _, sessions = _broker(_config([{"id": "auto", "effect": "ALLOW_AUTO"}]))
    bad = _op("cloudtrail", "StopLogging", {"Name": "trail"})
    assert broker.execute(_plan(bad), bad).status is ExecutionStatus.BLOCKED
    op = _op()
    tampered = _op(params={**UPDATE_PARAMS, "desiredCount": 0})
    rec = broker.execute(_plan(op), tampered)
    assert "not part of the plan" in rec.reason
    assert sessions.calls == 0


def test_api_rejection_failed_and_transport_error_ambiguous() -> None:
    broker, store, sessions = _broker(
        _config([{"id": "auto", "effect": "ALLOW_AUTO", "service": "ecs"}])
    )
    op = _op()
    plan = _plan(op)
    sessions.stubber.add_client_error("update_service", "ServiceNotFoundException", "nope")
    with sessions.stubber:
        assert broker.execute(plan, op).status is ExecutionStatus.FAILED

    def boom(**_: Any) -> Any:
        raise EndpointConnectionError(endpoint_url="https://ecs")

    sessions.client.update_service = boom  # type: ignore[method-assign]
    rec = broker.execute(plan, op)
    assert rec.status is ExecutionStatus.AMBIGUOUS and "reconcile" in rec.reason
    assert store.succeeded_execution(rec.operation_fingerprint) is None


def test_verifier_reads_back_state(cfg: AgentConfig) -> None:
    session = FixtureSession(
        {"ecs": {"DescribeServices": {"services": [{"serviceName": "api", "runningCount": 2}]}}}
    )
    reader = AwsReader(session, cfg.account("dev"))
    spec = VerificationSpec(
        service="ecs",
        operation="DescribeServices",
        params={"cluster": "dev-cluster", "services": ["api"]},
        expect={"services[0].runningCount": 2},
        attempts=2,
        delay_seconds=0,
    )
    op = _op().model_copy(update={"verify": spec})
    assert verify_aws(reader, op, sleep=lambda _: None)[0] is True
    bad = op.model_copy(
        update={"verify": spec.model_copy(update={"expect": {"services[0].runningCount": 3}})}
    )
    ok, detail = verify_aws(reader, bad, sleep=lambda _: None)
    assert not ok and "want 3" in detail
    assert get_path({"a": [{"b": 1}]}, "a[0].b") == 1


def test_kill_switch_freezes_writes() -> None:
    broker, store, sessions = _broker(
        _config([{"id": "auto", "effect": "ALLOW_AUTO", "service": "ecs"}])
    )
    store.set_writes_frozen(True, "admin")
    op = _op()
    rec = broker.execute(_plan(op), op)
    assert rec.status is ExecutionStatus.BLOCKED and "kill switch" in rec.reason
    assert sessions.calls == 0
