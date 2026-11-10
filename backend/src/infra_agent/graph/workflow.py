"""LangGraph investigation workflow: TRIAGE -> DISCOVER -> ANALYZE -> SUMMARIZE.

No node has side effects outside this process; there is no write node in Phase 1.
Dependencies are injected so tests run fully against fixtures.
"""

from __future__ import annotations

import operator
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.language_models import BaseChatModel
from langgraph.graph import END, START, StateGraph

from infra_agent.config import AgentConfig, ConfigError
from infra_agent.integrations.bedrock import narrate
from infra_agent.integrations.github_source import GitHubRunSource
from infra_agent.models.domain import AuditEvent, Evidence, Task, TaskStatus, utcnow
from infra_agent.models.report import DiagnosticReport, GitHubCorrelation
from infra_agent.playbooks.ecs_health.collector import EcsCollector, SessionLike
from infra_agent.playbooks.ecs_health.github_correlation import correlate
from infra_agent.playbooks.ecs_health.models import EcsSnapshot, Finding, ServiceHealth
from infra_agent.playbooks.ecs_health.rules import analyze
from infra_agent.security.credentials import IdentityCheck

_SERVICE_RE = re.compile(r"^[A-Za-z0-9_-]{1,255}$")

DeepDive = Callable[[str], Awaitable[str]]


class InvestigationRequest(TypedDict):
    account_alias: str
    region: str
    cluster: str
    services: list[str]
    repository: str | None
    question: str | None


class InvestigationState(TypedDict, total=False):
    request: InvestigationRequest
    task: Task
    snapshot: EcsSnapshot
    evidence: list[Evidence]
    findings: list[Finding]
    health: list[ServiceHealth]
    correlations: list[GitHubCorrelation]
    report: DiagnosticReport
    errors: Annotated[list[str], operator.add]
    audit: Annotated[list[AuditEvent], operator.add]


@dataclass
class InvestigationDeps:
    config: AgentConfig
    session: SessionLike
    mode: Literal["mock", "live"]
    identity: IdentityCheck | None = None
    github: GitHubRunSource | None = None
    model: BaseChatModel | None = None
    deep_dive: DeepDive | None = None


def _audit(
    task: Task,
    action: str,
    outcome: Literal["allowed", "denied", "error", "info"],
    detail: str = "",
) -> AuditEvent:
    return AuditEvent(
        actor="infra-agent",
        action=action,
        target=task.account_alias,
        outcome=outcome,
        correlation_id=task.id,
        detail=detail,
    )


def triage_errors(deps: InvestigationDeps, req: InvestigationRequest) -> list[str]:
    """Scope checks against operator config. Anything not configured is refused."""
    cfg = deps.config
    errors: list[str] = []
    try:
        account = cfg.account(req["account_alias"])
    except ConfigError as exc:
        return [str(exc)]
    if req["region"] not in account.allowed_regions:
        errors.append(f"region {req['region']} not allowed for account {account.alias}")
    if req["cluster"] not in account.allowed_clusters:
        errors.append(f"cluster {req['cluster']} not allowed for account {account.alias}")
    bad = [s for s in req["services"] if not _SERVICE_RE.match(s)]
    if bad:
        errors.append(f"invalid service names: {bad}")
    if len(req["services"]) > cfg.limits.max_services:
        errors.append(f"more than {cfg.limits.max_services} services requested")
    if req["repository"]:
        try:
            repo = cfg.repository(req["repository"])
            if repo.account_alias != account.alias:
                errors.append("repository is not mapped to this account")
        except ConfigError as exc:
            errors.append(str(exc))
    if deps.mode == "live" and not (deps.identity and deps.identity.ok):
        errors.append("live reads require a validated read-only identity")
    if deps.identity and deps.identity.account_alias != account.alias:
        errors.append("validated identity belongs to a different account alias")
    return errors


def build_graph(deps: InvestigationDeps) -> Any:
    cfg = deps.config

    def triage(state: InvestigationState) -> InvestigationState:
        task = state["task"]
        task.transition(TaskStatus.TRIAGING)
        errors = triage_errors(deps, state["request"])
        if errors:
            task.transition(TaskStatus.DENIED)
            return {
                "task": task,
                "errors": errors,
                "audit": [_audit(task, "triage", "denied", "; ".join(errors))],
            }
        task.transition(TaskStatus.INVESTIGATING)
        return {"task": task, "audit": [_audit(task, "triage", "allowed")]}

    def discover(state: InvestigationState) -> InvestigationState:
        req = state["request"]
        task = state["task"]
        collector = EcsCollector(
            deps.session,
            account_alias=req["account_alias"],
            region=req["region"],
            limits=cfg.limits,
        )
        try:
            snapshot = collector.collect(req["cluster"], req["services"] or None)
        except Exception as exc:
            task.transition(TaskStatus.FAILED)
            msg = f"discovery failed: {type(exc).__name__}: {exc}"
            return {
                "task": task,
                "errors": [msg],
                "audit": [_audit(task, "discover", "error", msg)],
            }
        detail = f"{len(snapshot.services)} service(s)"
        return {
            "snapshot": snapshot,
            "evidence": collector.evidence,
            "audit": [_audit(task, "discover", "info", detail)],
        }

    def analyze_node(state: InvestigationState) -> InvestigationState:
        req = state["request"]
        snapshot = state["snapshot"]
        findings, health = analyze(snapshot)
        correlations: list[GitHubCorrelation] = []
        evidence = list(state["evidence"])
        errors: list[str] = []
        if req["repository"] and deps.github is not None:
            starts = [
                d.created_at for s in snapshot.services for d in s.deployments if d.created_at
            ]
            window = timedelta(hours=cfg.limits.github_window_hours)
            since = (min(starts) if starts else snapshot.collected_at) - window
            try:
                runs = deps.github.list_workflow_runs(
                    req["repository"], since, snapshot.collected_at + window
                )
                correlations, gh_ev = correlate(
                    snapshot, req["repository"], runs, cfg.limits.github_window_hours
                )
                evidence += gh_ev
            except Exception as exc:
                errors.append(f"GitHub correlation failed: {type(exc).__name__}")
        return {
            "findings": findings,
            "health": health,
            "correlations": correlations,
            "evidence": evidence,
            "errors": errors,
        }

    async def summarize(state: InvestigationState) -> InvestigationState:
        req = state["request"]
        task = state["task"]
        snapshot = state["snapshot"]
        findings = state["findings"]
        health = state["health"]
        correlations = state.get("correlations", [])
        limitations = [
            "Findings come from ECS/ELB control-plane data only; CloudWatch Logs and "
            "metrics are not read in Phase 1.",
        ]
        if snapshot.truncated:
            limitations.append(f"Service list truncated to {cfg.limits.max_services}.")
        limitations += [f"Collection error: {e}" for e in snapshot.collection_errors]
        if req["repository"] and deps.github is None:
            limitations.append("GitHub correlation requested but no GitHub source configured.")
        limitations += state.get("errors", [])

        payload = {
            "cluster": snapshot.cluster,
            "region": snapshot.region,
            "services": [h.model_dump(mode="json") for h in health],
            "findings": [f.model_dump(mode="json") for f in findings],
            "correlations": [
                {
                    "service": c.service,
                    "relation": c.relation,
                    "workflow": c.run.workflow_name,
                    "conclusion": c.run.conclusion,
                    "sha": c.run.head_sha[:12],
                }
                for c in correlations
            ],
        }
        narrative = deterministic_narrative(health, findings)
        source: Literal["bedrock", "deterministic"] = "deterministic"
        if deps.model is not None:
            try:
                narrative, source = narrate(deps.model, payload), "bedrock"
            except Exception as exc:
                limitations.append(
                    f"Bedrock narrative failed ({type(exc).__name__}); deterministic summary used."
                )

        agent_notes = None
        if deps.deep_dive is not None:
            question = req["question"] or (
                f"Investigate ECS cluster {snapshot.cluster} in {snapshot.region}. "
                f"Known findings: {[f.title for f in findings]}. Look for additional evidence."
            )
            try:
                agent_notes = await deps.deep_dive(question)
            except Exception as exc:
                limitations.append(f"Agent deep-dive failed: {type(exc).__name__}: {exc}")

        report = DiagnosticReport(
            task_id=task.id,
            mode=deps.mode,
            account_alias=snapshot.account_alias,
            region=snapshot.region,
            cluster=snapshot.cluster,
            generated_at=utcnow(),
            caller_identity=deps.identity.display_arn if deps.identity else None,
            services=tuple(health),
            findings=tuple(findings),
            correlations=tuple(correlations),
            evidence=tuple(state["evidence"]),
            narrative=narrative,
            narrative_source=source,
            agent_notes=agent_notes,
            limitations=tuple(limitations),
        )
        task.transition(TaskStatus.COMPLETED)
        detail = f"{len(findings)} findings"
        return {"report": report, "task": task, "audit": [_audit(task, "report", "info", detail)]}

    def after_triage(state: InvestigationState) -> str:
        return "discover" if state["task"].status is TaskStatus.INVESTIGATING else END

    def after_discover(state: InvestigationState) -> str:
        return "analyze" if "snapshot" in state else END

    g = StateGraph(InvestigationState)
    g.add_node("triage", triage)
    g.add_node("discover", discover)
    g.add_node("analyze", analyze_node)
    g.add_node("summarize", summarize)
    g.add_edge(START, "triage")
    g.add_conditional_edges("triage", after_triage, ["discover", END])
    g.add_conditional_edges("discover", after_discover, ["analyze", END])
    g.add_edge("analyze", "summarize")
    g.add_edge("summarize", END)
    return g.compile()


def deterministic_narrative(health: list[ServiceHealth], findings: list[Finding]) -> str:
    if not health:
        return "No services were found in the cluster."
    by_id = {f.id: f for f in findings}
    lines = []
    for h in health:
        counts = f"{h.running_count}/{h.desired_count} running"
        if h.verdict == "HEALTHY":
            lines.append(f"- **{h.service}**: healthy ({counts}).")
            continue
        top = [by_id[i] for i in h.finding_ids][:3]
        causes = "; ".join(f"{f.title} [{f.id}]" for f in top)
        lines.append(f"- **{h.service}**: {h.verdict} ({counts}). {causes}.")
    return "\n".join(lines)


async def run_investigation(
    deps: InvestigationDeps, request: InvestigationRequest, requester: str = "operator"
) -> InvestigationState:
    task = Task(
        requester=requester,
        origin="cli",
        intent=request["question"] or f"Investigate ECS cluster {request['cluster']}",
        account_alias=request["account_alias"],
        region=request["region"],
        repositories=(request["repository"],) if request["repository"] else (),
    )
    graph = build_graph(deps)
    result: InvestigationState = await graph.ainvoke(
        {"request": request, "task": task, "errors": [], "audit": []},
        config={"recursion_limit": 10},
    )
    return result
