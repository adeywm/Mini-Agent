"""General-purpose operations workflow (LangGraph) with risk-based human approval.

    plan -> policy -> [request_approval -> await_approval(interrupt)] -> execute -> verify -> report

* The planner (LLM or caller) proposes operations; the server fixes account, environment,
  ids and idempotency keys and computes the plan digest. The model never picks an account,
  role or credentials.
* Any DENY denies the whole plan (no partial plans that silently drop steps).
* ``request_approval`` persists the approval request; ``await_approval`` contains ONLY the
  ``interrupt()`` and the atomic approval consumption, so a resumed node has no earlier side
  effect to repeat.
* Execution goes through the broker, which re-checks everything; READ steps use the read
  path. Each step is verified by reading state back. Unverifiable steps -> NEEDS_REVIEW.
* State is checkpointed (Postgres in production) under ``thread_id = task.id`` so a paused
  or interrupted task resumes after a restart.
"""

from __future__ import annotations

import operator
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, ConfigDict, Field

from infra_agent.aws.reader import AwsReader
from infra_agent.config import AgentConfig
from infra_agent.core.operations import AccessClass, OperationRef, OperationRequest, ResourceScope
from infra_agent.models.domain import (
    TERMINAL_STATUSES,
    ApprovalRequest,
    AuditEvent,
    ChangePlan,
    Decision,
    ExecutionRecord,
    ExecutionStatus,
    PlannedOperation,
    PolicyDecision,
    Task,
    TaskStatus,
    VerificationSpec,
    sha256_digest,
    utcnow,
)
from infra_agent.persistence.store import Store
from infra_agent.policy.engine import PolicyEngine, access_of


class ProposedOperation(BaseModel):
    """What a planner may propose. No account, environment, role or credential fields."""

    model_config = ConfigDict(extra="forbid")

    provider: Literal["aws", "github"] = "aws"
    service: str = Field(description="AWS botocore service, or HTTP method for GitHub")
    operation: str = Field(description="AWS operation (PascalCase), or GitHub REST path")
    region: str = Field(default="", description="AWS region; empty for GitHub")
    parameters: dict[str, Any] = Field(default_factory=dict)
    resource_scope: list[str] = Field(min_length=1)
    depends_on: list[int] = Field(default_factory=list, description="Indexes of earlier steps")
    expected_outcome: str
    verify: VerificationSpec | None = None
    estimated_cost_change_usd_monthly: float | None = None


class PlanProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    operations: list[ProposedOperation] = Field(min_length=1)


Planner = Callable[[Task], Awaitable[PlanProposal]]
Executor = Callable[[ChangePlan, PlannedOperation, str | None], ExecutionRecord]
Verifier = Callable[[PlannedOperation], tuple[bool, str]]


class OpsState(TypedDict, total=False):
    task: Task
    plan: ChangePlan
    proposal_summary: str
    decisions: list[PolicyDecision]
    approval_id: str | None
    records: list[ExecutionRecord]
    verifications: dict[str, str]
    outcome: str
    errors: Annotated[list[str], operator.add]


@dataclass
class OpsDeps:
    config: AgentConfig
    store: Store
    planner: Planner
    executors: dict[str, Executor]  # provider -> broker.execute
    verifiers: dict[str, Verifier]  # provider -> verify function
    readers: dict[str, AwsReader]  # account alias -> read path for READ steps


def build_plan(task: Task, proposal: PlanProposal, config: AgentConfig) -> ChangePlan:
    account = config.account(task.account_alias)
    if len(proposal.operations) > config.execution.max_operations_per_plan:
        raise ValueError("plan exceeds max_operations_per_plan")
    ops: list[PlannedOperation] = []
    for i, p in enumerate(proposal.operations):
        if any(d >= i or d < 0 for d in p.depends_on):
            raise ValueError(f"step {i} depends on a later or invalid step")
        params = dict(p.parameters)
        if p.provider == "github" and "repository" not in params:
            raise ValueError(f"step {i}: GitHub operations must declare parameters.repository")
        ops.append(
            PlannedOperation(
                operation_id=f"op-{i + 1:03d}",
                provider=p.provider,
                account_alias=account.alias,
                environment=account.environment,
                service=p.service,
                operation=p.operation,
                region=p.region or task.region,
                resource_scope=tuple(p.resource_scope),
                parameters=params,
                depends_on=tuple(f"op-{d + 1:03d}" for d in p.depends_on),
                expected_outcome=p.expected_outcome,
                verification=p.verify.operation if p.verify else "none",
                verify=p.verify,
                estimated_cost_change_usd_monthly=p.estimated_cost_change_usd_monthly,
                idempotency_key=sha256_digest({"task": task.id, "step": i})[7:31],
            )
        )
    return ChangePlan(id=f"plan-{task.id}", version=1, task_id=task.id, operations=tuple(ops))


# --- State codec ----------------------------------------------------------------------
# Graph state holds plain JSON so checkpoints round-trip exactly (nested Pydantic models
# are not reliably rebuilt by the checkpoint serializer). Nodes see models.
def _load(state: dict[str, Any]) -> dict[str, Any]:
    out = dict(state)
    if isinstance(out.get("task"), dict):
        out["task"] = Task.model_validate(out["task"])
    if isinstance(out.get("plan"), dict):
        out["plan"] = ChangePlan.model_validate(out["plan"])
    if "decisions" in out:
        out["decisions"] = [
            d if isinstance(d, PolicyDecision) else PolicyDecision.model_validate(d)
            for d in out["decisions"]
        ]
    if "records" in out:
        out["records"] = [
            r if isinstance(r, ExecutionRecord) else ExecutionRecord.model_validate(r)
            for r in out["records"]
        ]
    return out


def _dump(update: dict[str, Any]) -> dict[str, Any]:
    out = dict(update)
    if isinstance(out.get("task"), Task):
        out["task"] = out["task"].model_dump(mode="json")
    if isinstance(out.get("plan"), ChangePlan):
        out["plan"] = out["plan"].model_dump(mode="json", exclude={"digest"})
    if "decisions" in out:
        out["decisions"] = [d.model_dump(mode="json") for d in out["decisions"]]
    if "records" in out:
        out["records"] = [r.model_dump(mode="json") for r in out["records"]]
    return out


def _wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
    import inspect

    if inspect.iscoroutinefunction(fn):

        async def anode(state: dict[str, Any]) -> dict[str, Any]:
            return _dump(await fn(_load(state)))

        return anode

    def node(state: dict[str, Any]) -> Any:
        result = fn(_load(state))
        return _dump(result) if isinstance(result, dict) else result

    return node


def build_operations_graph(deps: OpsDeps, checkpointer: Any = None) -> Any:
    policy = PolicyEngine(deps.config)
    store = deps.store

    def audit(task: Task, action: str, outcome: str, detail: str = "") -> None:
        store.append_audit(
            [
                AuditEvent(
                    actor="operations-graph",
                    action=action,
                    target=task.account_alias,
                    outcome=outcome,  # type: ignore[arg-type]
                    correlation_id=task.id,
                    detail=detail[:500],
                )
            ]
        )

    def save(task: Task, status: TaskStatus) -> Task:
        if task.status is not status:
            task.transition(status)
            if status in TERMINAL_STATUSES or status is TaskStatus.NEEDS_REVIEW:
                from infra_agent.observability import tasks_finished

                tasks_finished.add(1, {"status": status.value})
        store.save_task(task)
        return task

    async def plan_node(state: OpsState) -> OpsState:
        task = state["task"]
        for s in (TaskStatus.TRIAGING, TaskStatus.INVESTIGATING, TaskStatus.PLANNING):
            save(task, s)
        try:
            proposal = await deps.planner(task)
            plan = build_plan(task, proposal, deps.config)
        except Exception as exc:
            save(task, TaskStatus.FAILED)
            audit(task, "plan", "error", str(exc))
            return {"task": task, "outcome": "planning failed", "errors": [f"planning: {exc}"]}
        audit(task, "plan", "info", f"{len(plan.operations)} step(s) {plan.digest}")
        return {"task": task, "plan": plan, "proposal_summary": proposal.summary}

    def policy_node(state: OpsState) -> OpsState:
        task, plan = state["task"], state["plan"]
        save(task, TaskStatus.POLICY_REVIEW)
        decisions = [policy.evaluate(op, plan.digest) for op in plan.operations]
        from infra_agent.observability import policy_decisions

        for d in decisions:
            policy_decisions.add(1, {"decision": d.decision.value})
        denied = [d for d in decisions if d.decision is Decision.DENY]
        if denied:
            save(task, TaskStatus.DENIED)
            reasons = "; ".join(f"{d.operation_id}: {d.reason}" for d in denied)
            audit(task, "policy", "denied", reasons)
            return {"task": task, "decisions": decisions, "outcome": f"denied - {reasons}"}
        audit(
            task,
            "policy",
            "allowed",
            ", ".join(f"{d.operation_id}={d.decision}" for d in decisions),
        )
        return {"task": task, "decisions": decisions}

    def request_approval(state: OpsState) -> OpsState:
        task, plan = state["task"], state["plan"]
        needs = tuple(
            d.operation_id for d in state["decisions"] if d.decision is Decision.REQUIRE_APPROVAL
        )
        approval_id = f"apr-{plan.digest[7:23]}"
        store.save_plan(plan)
        if store.get_approval(approval_id) is None:
            store.save_approval(
                ApprovalRequest(
                    id=approval_id,
                    plan_digest=plan.digest,
                    task_id=task.id,
                    scope=needs,
                    requester=task.requester,
                    expires_at=utcnow()
                    + timedelta(minutes=deps.config.execution.approval_ttl_minutes),
                )
            )
        save(task, TaskStatus.AWAITING_APPROVAL)
        audit(task, "approval", "info", f"requested {approval_id} for {', '.join(needs)}")
        return {"task": task, "approval_id": approval_id}

    def await_approval(state: OpsState) -> OpsState:
        task, plan = state["task"], state["plan"]
        answer = interrupt(
            {
                "type": "approval_required",
                "task_id": task.id,
                "approval_id": state["approval_id"],
                "plan_digest": plan.digest,
                "operations": [op.model_dump(mode="json") for op in plan.operations],
                "decisions": [d.model_dump(mode="json") for d in state["decisions"]],
            }
        )
        # The resume payload is NOT authorization; the persisted approval record is.
        approval_id = state["approval_id"] or ""
        consumed = None
        if isinstance(answer, dict) and answer.get("approval_id") == approval_id:
            consumed = store.consume_approval(approval_id, plan.digest)
        if consumed is None:
            record = store.get_approval(approval_id)
            status = (
                TaskStatus.EXPIRED
                if record is not None and record.expires_at <= utcnow()
                else TaskStatus.REJECTED
            )
            save(task, status)
            audit(task, "approval", "denied", f"approval {approval_id} not usable")
            return {"task": task, "outcome": f"{status.value.lower()} - no execution"}
        save(task, TaskStatus.APPROVED)
        audit(task, "approval", "allowed", f"{approval_id} approved by {consumed.approver}")
        return {"task": task}

    def auto_approve(state: OpsState) -> OpsState:
        task = save(state["task"], TaskStatus.APPROVED)
        return {"task": task, "approval_id": None}

    def execute_node(state: OpsState) -> OpsState:
        task, plan = state["task"], state["plan"]
        save(task, TaskStatus.EXECUTING)
        records: list[ExecutionRecord] = []
        done: set[str] = set()
        for op in plan.operations:
            if not set(op.depends_on) <= done:
                break
            if op.provider == "aws" and access_of(op) is AccessClass.READ:
                rec = _read_step(deps, plan, op)
            else:
                executor = deps.executors.get(op.provider)
                if executor is None:
                    rec = ExecutionRecord(
                        task_id=task.id,
                        operation_id=op.operation_id,
                        plan_digest=plan.digest,
                        operation_fingerprint="-",
                        status=ExecutionStatus.BLOCKED,
                        reason=f"no executor for {op.provider}",
                    )
                else:
                    rec = executor(plan, op, state.get("approval_id"))
            records.append(rec)
            if rec.status is not ExecutionStatus.SUCCEEDED:
                break
            done.add(op.operation_id)
        return {"task": task, "records": records}

    def verify_node(state: OpsState) -> OpsState:
        task, plan, records = state["task"], state["plan"], state["records"]
        save(task, TaskStatus.VERIFYING)
        ok_ids = {r.operation_id for r in records if r.status is ExecutionStatus.SUCCEEDED}
        results: dict[str, str] = {}
        verified_all = True
        for op in plan.operations:
            if op.operation_id not in ok_ids:
                continue
            if op.provider == "aws" and access_of(op) is AccessClass.READ:
                results[op.operation_id] = "read step"
                continue
            verifier = deps.verifiers.get(op.provider)
            ok, detail = verifier(op) if verifier else (False, "no verifier")
            results[op.operation_id] = detail
            verified_all &= ok
        failed = [r for r in records if r.status is not ExecutionStatus.SUCCEEDED]
        all_ran = len(ok_ids) == len(plan.operations)
        if all_ran and verified_all:
            final, outcome = TaskStatus.COMPLETED, "completed and verified"
        elif any(r.status is ExecutionStatus.AMBIGUOUS for r in failed) or (
            all_ran and not verified_all
        ):
            final, outcome = TaskStatus.NEEDS_REVIEW, "outcome uncertain - needs review"
        elif ok_ids and any(
            access_of(o) is not AccessClass.READ
            for o in plan.operations
            if o.operation_id in ok_ids
        ):
            final, outcome = TaskStatus.PARTIALLY_COMPLETED, "partially completed - recovery needed"
        else:
            final, outcome = TaskStatus.FAILED, "failed - no changes made"
        if failed:
            outcome += f" ({failed[0].operation_id}: {failed[0].reason[:200]})"
        save(task, final)
        audit(task, "verify", "info" if final is TaskStatus.COMPLETED else "error", outcome)
        return {"task": task, "verifications": results, "outcome": outcome}

    def after_plan(state: OpsState) -> str:
        return "policy" if "plan" in state else END

    def after_policy(state: OpsState) -> str:
        if state["task"].status is TaskStatus.DENIED:
            return END
        needs = any(d.decision is Decision.REQUIRE_APPROVAL for d in state["decisions"])
        return "request_approval" if needs else "auto_approve"

    def after_await(state: OpsState) -> str:
        return "execute" if state["task"].status is TaskStatus.APPROVED else END

    g = StateGraph(OpsState)
    g.add_node("plan", _wrap(plan_node))
    g.add_node("policy", _wrap(policy_node))
    g.add_node("request_approval", _wrap(request_approval))
    g.add_node("await_approval", _wrap(await_approval))
    g.add_node("auto_approve", _wrap(auto_approve))
    g.add_node("execute", _wrap(execute_node))
    g.add_node("verify", _wrap(verify_node))
    g.add_edge(START, "plan")
    g.add_conditional_edges("plan", _wrap(after_plan), ["policy", END])
    g.add_conditional_edges(
        "policy", _wrap(after_policy), ["request_approval", "auto_approve", END]
    )
    g.add_edge("request_approval", "await_approval")
    g.add_conditional_edges("await_approval", _wrap(after_await), ["execute", END])
    g.add_edge("auto_approve", "execute")
    g.add_edge("execute", "verify")
    g.add_edge("verify", END)
    return g.compile(checkpointer=checkpointer)


def _read_step(deps: OpsDeps, plan: ChangePlan, op: PlannedOperation) -> ExecutionRecord:
    base: dict[str, Any] = {
        "task_id": plan.task_id,
        "operation_id": op.operation_id,
        "plan_digest": plan.digest,
        "operation_fingerprint": sha256_digest(op.model_dump(mode="json")),
    }
    reader = deps.readers.get(op.account_alias)
    if reader is None:
        return ExecutionRecord(**base, status=ExecutionStatus.BLOCKED, reason="no read path")
    try:
        result = reader.call(
            OperationRequest(
                ref=OperationRef(provider="aws", service=op.service, operation=op.operation),
                scope=ResourceScope(account_alias=op.account_alias, region=op.region),
                params=op.parameters,
            )
        )
    except Exception as exc:
        return ExecutionRecord(
            **base, status=ExecutionStatus.FAILED, reason=f"{type(exc).__name__}: {exc}"
        )
    return ExecutionRecord(
        **base, status=ExecutionStatus.SUCCEEDED, reason="read", result=result.data
    )


def thread_config(task_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": task_id}, "recursion_limit": 25}


async def start_task(graph: Any, task: Task) -> dict[str, Any]:
    result: dict[str, Any] = await graph.ainvoke(
        {"task": task.model_dump(mode="json"), "errors": []}, thread_config(task.id)
    )
    return _load(result)


async def resume_task(graph: Any, task_id: str, approval_id: str) -> dict[str, Any]:
    result: dict[str, Any] = await graph.ainvoke(
        Command(resume={"approval_id": approval_id}), thread_config(task_id)
    )
    return _load(result)
