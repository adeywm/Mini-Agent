"""Execution broker interface + Phase 0/1 write-disabled implementation.

The broker is the only component that will ever be allowed to execute writes (Phase 4+).
In this phase it refuses every operation, records why, and holds no AWS client or
credentials at all, so there is no code path from the agent to a mutating AWS API.
"""

from __future__ import annotations

from typing import Protocol

from infra_agent.models.domain import (
    ApprovalRequest,
    ChangePlan,
    ExecutionRecord,
    ExecutionStatus,
    PlannedOperation,
    sha256_digest,
)


class ExecutionBroker(Protocol):
    @property
    def writes_enabled(self) -> bool: ...

    def execute(
        self, plan: ChangePlan, operation: PlannedOperation, approval: ApprovalRequest | None
    ) -> ExecutionRecord: ...


class MockExecutionBroker:
    """Refuses everything. Records each attempt so tests can assert nothing ran."""

    def __init__(self) -> None:
        self.records: list[ExecutionRecord] = []

    @property
    def writes_enabled(self) -> bool:
        return False

    def execute(
        self, plan: ChangePlan, operation: PlannedOperation, approval: ApprovalRequest | None
    ) -> ExecutionRecord:
        record = ExecutionRecord(
            task_id=plan.task_id,
            operation_id=operation.operation_id,
            plan_digest=plan.digest,
            operation_fingerprint=sha256_digest(operation.model_dump(mode="json")),
            status=ExecutionStatus.BLOCKED,
            reason="AWS/GitHub writes are disabled in Phase 1; no external call was made",
        )
        self.records.append(record)
        return record
