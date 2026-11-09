from __future__ import annotations

from datetime import UTC, datetime

import pytest

from infra_agent.models.domain import (
    TERMINAL_STATUSES,
    TRANSITIONS,
    ChangePlan,
    Evidence,
    InvalidTransition,
    PlannedOperation,
    Task,
    TaskStatus,
    canonical_json,
)


def _op(**over: object) -> PlannedOperation:
    base: dict[str, object] = {
        "operation_id": "op-001",
        "provider": "aws",
        "account_alias": "dev",
        "environment": "development",
        "service": "ecs",
        "operation": "UpdateService",
        "region": "ap-south-1",
        "resource_scope": ("ecs:dev-cluster/api",),
        "parameters": {"cluster": "dev-cluster", "service": "api", "forceNewDeployment": True},
        "expected_outcome": "new deployment",
        "verification": "steady state",
        "idempotency_key": "srv-1",
    }
    base.update(over)
    return PlannedOperation.model_validate(base)


def _task() -> Task:
    return Task(requester="op", origin="cli", intent="x", account_alias="dev", region="ap-south-1")


def test_valid_transition_path() -> None:
    t = _task()
    for s in (TaskStatus.TRIAGING, TaskStatus.INVESTIGATING, TaskStatus.COMPLETED):
        t.transition(s)
    assert t.status is TaskStatus.COMPLETED
    assert len(t.history) == 3


@pytest.mark.parametrize(
    "target", [TaskStatus.EXECUTING, TaskStatus.APPROVED, TaskStatus.COMPLETED]
)
def test_cannot_jump_to_execution_states(target: TaskStatus) -> None:
    t = _task()
    with pytest.raises(InvalidTransition):
        t.transition(target)


def test_terminal_states_have_no_exits() -> None:
    for s in TERMINAL_STATUSES:
        assert not TRANSITIONS.get(s)


def test_execution_only_reachable_via_approval() -> None:
    sources = {s for s, targets in TRANSITIONS.items() if TaskStatus.EXECUTING in targets}
    assert sources == {TaskStatus.APPROVED}


def test_plan_digest_deterministic_and_sensitive() -> None:
    a = ChangePlan(id="p1", version=1, task_id="t1", operations=(_op(),))
    b = ChangePlan.model_validate_json(a.model_dump_json(exclude={"digest"}))
    assert a.digest == b.digest
    assert a.digest.startswith("sha256:")
    for change in (
        {"region": "us-east-1"},
        {"parameters": {"cluster": "dev-cluster", "service": "web", "forceNewDeployment": True}},
        {"account_alias": "prod"},
        {"resource_scope": ("ecs:dev-cluster/*",)},
    ):
        c = ChangePlan(id="p1", version=1, task_id="t1", operations=(_op(**change),))
        assert c.digest != a.digest, change
    assert ChangePlan(id="p1", version=2, task_id="t1", operations=(_op(),)).digest != a.digest


def test_canonical_json_is_order_independent() -> None:
    assert canonical_json({"b": 1, "a": [2, {"d": 1, "c": 2}]}) == canonical_json(
        {"a": [2, {"c": 2, "d": 1}], "b": 1}
    )
    with pytest.raises(ValueError):
        canonical_json({"x": float("nan")})


def test_evidence_digest_changes_with_data() -> None:
    now = datetime.now(UTC)
    e1 = Evidence(id="e", source="s", collected_at=now, summary="x", data={"a": 1})
    e2 = Evidence(id="e", source="s", collected_at=now, summary="x", data={"a": 2})
    assert e1.digest != e2.digest
