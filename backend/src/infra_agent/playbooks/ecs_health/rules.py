"""Deterministic ECS diagnosis. Every finding cites the evidence IDs it was derived from.

The LLM never decides findings; it only narrates them. Text inside AWS events/stopped
reasons is matched as data and never interpreted as instructions.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Literal

from infra_agent.playbooks.ecs_health.models import (
    EcsSnapshot,
    Finding,
    FindingCategory,
    ServiceHealth,
    ServiceSnapshot,
    Severity,
    StoppedTask,
)

_C = FindingCategory


def _task_ev(task: StoppedTask) -> str:
    return f"ev-task-{task.task_arn.rsplit('/', 1)[-1][:12]}"


def _tg_ev(arn: str) -> str:
    return f"ev-tg-{arn.split(':')[-1].split('/')[1]}" if "/" in arn else f"ev-tg-{arn}"


_TASK_RULES: tuple[tuple[FindingCategory, Severity, re.Pattern[str], str], ...] = (
    (
        _C.IMAGE_PULL_FAILURE,
        Severity.CRITICAL,
        re.compile(r"CannotPullContainer|pull image manifest|image.*not found", re.I),
        "Tasks cannot pull the container image",
    ),
    (
        _C.TASK_INIT_FAILURE,
        Severity.CRITICAL,
        re.compile(r"ResourceInitializationError|unable to pull secrets|AccessDenied", re.I),
        "Tasks fail during initialization (secrets/registry/permissions)",
    ),
    (
        _C.OUT_OF_MEMORY,
        Severity.CRITICAL,
        re.compile(r"OutOfMemory|OOM", re.I),
        "Containers are killed for exceeding memory",
    ),
    (
        _C.HEALTH_CHECK_FAILURE,
        Severity.CRITICAL,
        re.compile(r"failed (container|ELB) health checks", re.I),
        "Tasks are stopped by failing health checks",
    ),
)


class _Builder:
    def __init__(self, service: str) -> None:
        self.service = service
        self.findings: list[Finding] = []

    def add(
        self,
        category: FindingCategory,
        severity: Severity,
        title: str,
        detail: str,
        evidence: Iterable[str],
    ) -> None:
        ev = tuple(dict.fromkeys(evidence))
        if not ev:
            return
        self.findings.append(
            Finding(
                id=f"F-{self.service}-{len(self.findings) + 1:02d}",
                service=self.service,
                category=category,
                severity=severity,
                title=title,
                detail=detail,
                evidence_ids=ev,
            )
        )


def analyze_service(svc: ServiceSnapshot) -> tuple[list[Finding], ServiceHealth]:
    b = _Builder(svc.name)
    svc_ev = f"ev-svc-{svc.name}"

    if svc.status != "ACTIVE":
        b.add(
            _C.SERVICE_INACTIVE,
            Severity.WARNING,
            f"Service status is {svc.status}",
            "The service is not ACTIVE.",
            [svc_ev],
        )

    if svc.running_count < svc.desired_count:
        b.add(
            _C.SERVICE_UNDERSCALED,
            Severity.CRITICAL if svc.running_count == 0 else Severity.WARNING,
            f"Running {svc.running_count} of {svc.desired_count} desired tasks",
            f"{svc.pending_count} task(s) pending.",
            [svc_ev],
        )

    for d in svc.deployments:
        if d.rollout_state == "FAILED":
            b.add(
                _C.DEPLOYMENT_FAILED,
                Severity.CRITICAL,
                f"Deployment {d.id} failed",
                f"Task definition {d.task_definition}; reason: {d.rollout_state_reason or 'n/a'}; "
                f"failed tasks: {d.failed_tasks}.",
                [svc_ev],
            )
        elif d.rollout_state == "IN_PROGRESS" and d.failed_tasks > 0:
            b.add(
                _C.DEPLOYMENT_STALLED,
                Severity.WARNING,
                f"Deployment {d.id} in progress with {d.failed_tasks} failed task(s)",
                f"Task definition {d.task_definition}; running {d.running_count}/{d.desired_count}"
                + ("" if svc.circuit_breaker_enabled else "; deployment circuit breaker is OFF")
                + ".",
                [svc_ev],
            )

    # Stopped tasks: group by rule so one finding cites all matching tasks.
    matched: dict[FindingCategory, list[str]] = {}
    examples: dict[FindingCategory, str] = {}
    exits: list[str] = []
    exit_examples: list[str] = []
    for task in svc.stopped_tasks:
        texts = [task.stopped_reason or ""] + [c.reason or "" for c in task.containers]
        blob = " | ".join(t for t in texts if t)
        hit = False
        for cat, _sev, pattern, _title in _TASK_RULES:
            if pattern.search(blob) or (
                cat is _C.OUT_OF_MEMORY and any(c.exit_code == 137 for c in task.containers)
            ):
                matched.setdefault(cat, []).append(_task_ev(task))
                examples.setdefault(cat, blob[:300])
                hit = True
        if not hit and "Essential container in task exited" in (task.stopped_reason or ""):
            codes = [
                f"{c.name}={c.exit_code}" for c in task.containers if c.exit_code not in (0, None)
            ]
            if codes:
                exits.append(_task_ev(task))
                exit_examples.append(", ".join(codes))
    for cat, sev, _pattern, title in _TASK_RULES:
        if cat in matched:
            b.add(
                cat,
                sev,
                title,
                f"{len(matched[cat])} stopped task(s). Example: {examples[cat]}",
                matched[cat],
            )
    if exits:
        b.add(
            _C.CONTAINER_EXIT,
            Severity.CRITICAL,
            "Essential container exits with an error",
            f"{len(exits)} stopped task(s); exit codes: {'; '.join(sorted(set(exit_examples)))}.",
            exits,
        )

    unhealthy = [
        t for t in svc.target_health if t.state in ("unhealthy", "draining", "unavailable")
    ]
    if unhealthy:
        reasons = sorted(
            {f"{t.reason or t.state}: {t.description or ''}".strip() for t in unhealthy}
        )
        b.add(
            _C.TARGETS_UNHEALTHY,
            Severity.CRITICAL if len(unhealthy) == len(svc.target_health) else Severity.WARNING,
            f"{len(unhealthy)} of {len(svc.target_health)} load balancer target(s) not healthy",
            "; ".join(reasons),
            [_tg_ev(t.target_group_arn) for t in unhealthy],
        )

    placement = [e for e in svc.events if re.search(r"unable to place a task", e.message, re.I)]
    if placement:
        b.add(
            _C.PLACEMENT_FAILURE,
            Severity.CRITICAL,
            "ECS cannot place tasks",
            placement[0].message[:300],
            [f"ev-event-{e.id[:12]}" for e in placement],
        )

    health_events = [
        e for e in svc.events if re.search(r"failed (ELB|container) health checks", e.message, re.I)
    ]
    if health_events and _C.HEALTH_CHECK_FAILURE not in matched:
        b.add(
            _C.HEALTH_CHECK_FAILURE,
            Severity.CRITICAL,
            "Service events report failed health checks",
            health_events[0].message[:300],
            [f"ev-event-{e.id[:12]}" for e in health_events],
        )

    critical = any(f.severity is Severity.CRITICAL for f in b.findings)
    verdict: Literal["HEALTHY", "DEGRADED", "UNHEALTHY"] = (
        "UNHEALTHY"
        if critical or (svc.running_count == 0 and svc.desired_count > 0)
        else "DEGRADED"
        if b.findings
        else "HEALTHY"
    )
    return b.findings, ServiceHealth(
        service=svc.name,
        verdict=verdict,
        desired_count=svc.desired_count,
        running_count=svc.running_count,
        finding_ids=tuple(f.id for f in b.findings),
    )


def analyze(snapshot: EcsSnapshot) -> tuple[list[Finding], list[ServiceHealth]]:
    findings: list[Finding] = []
    health: list[ServiceHealth] = []
    for svc in snapshot.services:
        f, h = analyze_service(svc)
        findings += f
        health.append(h)
    return findings, health
