"""Read-only ECS/ELB evidence collector.

The same collector runs against:

* a real ``boto3.Session`` (live reads, after credential validation), or
* a ``FixtureSession`` that serves recorded AWS API responses (default, tests, demos).

Both go through ``read_guard`` so any non-allowlisted operation fails closed. Only the AWS
operations in ``read_guard.READ_ALLOWLIST`` are called, with server-built parameters; no
model-generated input ever reaches these calls.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from infra_agent.config import LimitsConfig
from infra_agent.models.domain import Evidence
from infra_agent.models.ecs import (
    ContainerState,
    Deployment,
    EcsSnapshot,
    ServiceEvent,
    ServiceSnapshot,
    StoppedTask,
    TargetHealth,
)
from infra_agent.security import read_guard
from infra_agent.security.redaction import redact, redact_text


class SessionLike(Protocol):
    def client(self, service_name: str, /, **kwargs: Any) -> Any: ...


def _snake(op: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", op).lower()


class FixtureClient:
    """Serves recorded responses. Keyed lookups mimic per-resource AWS calls."""

    _KEY_PARAM = {
        "DescribeTaskDefinition": "taskDefinition",
        "DescribeTargetHealth": "TargetGroupArn",
        "ListTasks": "serviceName",
    }

    def __init__(self, service: str, responses: dict[str, Any]) -> None:
        self._service = service
        self._responses = responses
        self._ops = {_snake(op): op for op in responses}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        # Map snake_case method -> PascalCase operation; unknown ops are still guarded.
        op = self._ops.get(name) or "".join(p.capitalize() for p in name.split("_"))

        def call(**params: Any) -> Any:
            read_guard.check_operation(self._service, op)
            self.calls.append((op, params))
            if op not in self._responses:
                raise LookupError(f"no fixture for {self._service}:{op}")
            resp = self._responses[op]
            key = self._KEY_PARAM.get(op)
            if key is not None:
                return resp.get(str(params.get(key)), _empty_for(op))
            return resp

        return call


def _empty_for(op: str) -> dict[str, Any]:
    return {"ListTasks": {"taskArns": []}, "DescribeTargetHealth": {"TargetHealthDescriptions": []}}.get(
        op, {}
    )


class FixtureSession:
    def __init__(self, data: dict[str, dict[str, Any]]) -> None:
        self._data = data
        self.clients: dict[str, FixtureClient] = {}

    @classmethod
    def from_file(cls, path: Path) -> FixtureSession:
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def client(self, service_name: str, /, **_: Any) -> FixtureClient:
        if service_name not in self.clients:
            self.clients[service_name] = FixtureClient(service_name, self._data.get(service_name, {}))
        return self.clients[service_name]


def _dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _short(arn: str) -> str:
    return arn.rsplit("/", 1)[-1]


class EcsCollector:
    def __init__(
        self,
        session: SessionLike,
        *,
        account_alias: str,
        region: str,
        limits: LimitsConfig,
    ) -> None:
        self._session = session
        self._account_alias = account_alias
        self._region = region
        self._limits = limits
        self.evidence: list[Evidence] = []
        self._now = datetime.now(UTC)

    def _client(self, service: str) -> Any:
        return self._session.client(service, region_name=self._region)

    def _record(self, ev_id: str, source: str, summary: str, data: dict[str, Any]) -> str:
        self.evidence.append(
            Evidence(
                id=ev_id,
                source=source,
                collected_at=self._now,
                summary=redact_text(summary),
                data=redact(data),
            )
        )
        return ev_id

    def collect(self, cluster: str, services: list[str] | None = None) -> EcsSnapshot:
        ecs = self._client("ecs")
        errors: list[str] = []

        clusters = ecs.describe_clusters(clusters=[cluster]).get("clusters", [])
        if not clusters:
            raise LookupError(f"cluster {cluster!r} not found in {self._region}")
        cluster_status = str(clusters[0].get("status", "UNKNOWN"))
        self._record(
            f"ev-cluster-{cluster}",
            "aws:ecs:DescribeClusters",
            f"cluster {cluster} status {cluster_status}",
            {
                "status": cluster_status,
                "activeServicesCount": clusters[0].get("activeServicesCount"),
                "runningTasksCount": clusters[0].get("runningTasksCount"),
            },
        )

        truncated = False
        if services:
            names = list(services)
        else:
            names = []
            token: str | None = None
            while True:
                kwargs: dict[str, Any] = {"cluster": cluster, "maxResults": 100}
                if token:
                    kwargs["nextToken"] = token
                page = ecs.list_services(**kwargs)
                names += [_short(a) for a in page.get("serviceArns", [])]
                token = page.get("nextToken")
                if not token or len(names) >= self._limits.max_services:
                    break
        if len(names) > self._limits.max_services:
            names = names[: self._limits.max_services]
            truncated = True

        snapshots: list[ServiceSnapshot] = []
        for i in range(0, len(names), 10):  # DescribeServices accepts up to 10
            resp = ecs.describe_services(cluster=cluster, services=names[i : i + 10])
            for failure in resp.get("failures", []):
                errors.append(f"DescribeServices {failure.get('arn')}: {failure.get('reason')}")
            for raw in resp.get("services", []):
                try:
                    snapshots.append(self._service(ecs, cluster, raw))
                except Exception as exc:  # keep partial evidence; never guess
                    errors.append(f"service {raw.get('serviceName')}: {type(exc).__name__}: {exc}")

        return EcsSnapshot(
            account_alias=self._account_alias,
            region=self._region,
            cluster=cluster,
            cluster_status=cluster_status,
            collected_at=self._now,
            services=tuple(snapshots),
            truncated=truncated,
            collection_errors=tuple(redact_text(e) for e in errors),
        )

    def _service(self, ecs: Any, cluster: str, raw: dict[str, Any]) -> ServiceSnapshot:
        name = str(raw["serviceName"])
        events = tuple(
            ServiceEvent(id=str(e["id"]), created_at=_dt(e["createdAt"]) or self._now,
                         message=redact_text(str(e["message"])))
            for e in raw.get("events", [])[: self._limits.max_service_events]
        )
        deployments = tuple(
            Deployment(
                id=str(d["id"]),
                status=str(d.get("status")),
                rollout_state=d.get("rolloutState"),
                rollout_state_reason=redact_text(d["rolloutStateReason"])
                if d.get("rolloutStateReason") else None,
                task_definition=str(d.get("taskDefinition")),
                desired_count=int(d.get("desiredCount", 0)),
                running_count=int(d.get("runningCount", 0)),
                pending_count=int(d.get("pendingCount", 0)),
                failed_tasks=int(d.get("failedTasks", 0)),
                created_at=_dt(d.get("createdAt")),
                updated_at=_dt(d.get("updatedAt")),
            )
            for d in raw.get("deployments", [])
        )
        cb = (raw.get("deploymentConfiguration") or {}).get("deploymentCircuitBreaker") or {}
        tg_arns = tuple(
            str(lb["targetGroupArn"]) for lb in raw.get("loadBalancers", []) if lb.get("targetGroupArn")
        )
        task_def = str(raw.get("taskDefinition"))

        self._record(
            f"ev-svc-{name}",
            "aws:ecs:DescribeServices",
            f"service {name}: status {raw.get('status')}, running {raw.get('runningCount')}/"
            f"{raw.get('desiredCount')}, {len(deployments)} deployment(s)",
            {
                "status": raw.get("status"),
                "desiredCount": raw.get("desiredCount"),
                "runningCount": raw.get("runningCount"),
                "pendingCount": raw.get("pendingCount"),
                "taskDefinition": task_def,
                "deployments": [d.model_dump(mode="json") for d in deployments],
            },
        )
        for ev in events:
            self._record(f"ev-event-{ev.id[:12]}", "aws:ecs:DescribeServices#events",
                         ev.message, {"service": name, "createdAt": ev.created_at.isoformat()})

        stopped = self._stopped_tasks(ecs, cluster, name)
        images = self._images(ecs, task_def)
        health = self._target_health(name, tg_arns)

        return ServiceSnapshot(
            cluster=cluster,
            name=name,
            arn=str(raw.get("serviceArn", name)),
            status=str(raw.get("status")),
            launch_type=raw.get("launchType"),
            desired_count=int(raw.get("desiredCount", 0)),
            running_count=int(raw.get("runningCount", 0)),
            pending_count=int(raw.get("pendingCount", 0)),
            task_definition=task_def,
            circuit_breaker_enabled=bool(cb.get("enable", False)),
            target_group_arns=tg_arns,
            deployments=deployments,
            events=events,
            stopped_tasks=stopped,
            target_health=health,
            container_images=images,
        )

    def _stopped_tasks(self, ecs: Any, cluster: str, service: str) -> tuple[StoppedTask, ...]:
        arns = ecs.list_tasks(
            cluster=cluster, serviceName=service, desiredStatus="STOPPED",
            maxResults=self._limits.max_stopped_tasks_per_service,
        ).get("taskArns", [])[: self._limits.max_stopped_tasks_per_service]
        if not arns:
            return ()
        out: list[StoppedTask] = []
        for t in ecs.describe_tasks(cluster=cluster, tasks=arns).get("tasks", []):
            task = StoppedTask(
                task_arn=str(t["taskArn"]),
                task_definition=str(t.get("taskDefinitionArn")),
                stopped_reason=redact_text(str(t["stoppedReason"])) if t.get("stoppedReason") else None,
                stop_code=t.get("stopCode"),
                stopped_at=_dt(t.get("stoppedAt")),
                containers=tuple(
                    ContainerState(
                        name=str(c.get("name")),
                        image=c.get("image"),
                        last_status=c.get("lastStatus"),
                        exit_code=c.get("exitCode"),
                        reason=redact_text(str(c["reason"])) if c.get("reason") else None,
                        health_status=c.get("healthStatus"),
                    )
                    for c in t.get("containers", [])
                ),
            )
            out.append(task)
            self._record(
                f"ev-task-{_short(task.task_arn)[:12]}",
                "aws:ecs:DescribeTasks",
                f"stopped task of {service}: {task.stop_code or '-'} / {task.stopped_reason or '-'}",
                task.model_dump(mode="json"),
            )
        return tuple(out)

    def _images(self, ecs: Any, task_def: str) -> tuple[str, ...]:
        td = ecs.describe_task_definition(taskDefinition=task_def).get("taskDefinition", {})
        # Only image + name; environment/secrets/command are never copied.
        images = tuple(str(c["image"]) for c in td.get("containerDefinitions", []) if c.get("image"))
        if td:
            self._record(
                f"ev-taskdef-{_short(task_def).replace(':', '-')}",
                "aws:ecs:DescribeTaskDefinition",
                f"task definition {_short(task_def)} images: {', '.join(images) or '-'}",
                {
                    "images": list(images),
                    "cpu": td.get("cpu"),
                    "memory": td.get("memory"),
                    "containers": [
                        {"name": c.get("name"), "memory": c.get("memory"),
                         "essential": c.get("essential"),
                         "healthCheck": bool(c.get("healthCheck"))}
                        for c in td.get("containerDefinitions", [])
                    ],
                },
            )
        return images

    def _target_health(self, service: str, tg_arns: tuple[str, ...]) -> tuple[TargetHealth, ...]:
        if not tg_arns:
            return ()
        elb = self._client("elbv2")
        out: list[TargetHealth] = []
        for tg in tg_arns:
            descs = elb.describe_target_health(TargetGroupArn=tg).get("TargetHealthDescriptions", [])
            entries = [
                TargetHealth(
                    target_group_arn=tg,
                    target_id=str(d.get("Target", {}).get("Id")),
                    port=d.get("Target", {}).get("Port"),
                    state=str(d.get("TargetHealth", {}).get("State", "unknown")),
                    reason=d.get("TargetHealth", {}).get("Reason"),
                    description=d.get("TargetHealth", {}).get("Description"),
                )
                for d in descs
            ]
            out += entries
            tg_name = tg.split(":")[-1].split("/")[1] if "/" in tg else tg
            self._record(
                f"ev-tg-{tg_name}",
                "aws:elbv2:DescribeTargetHealth",
                f"target group {tg_name} ({service}): "
                + (", ".join(f"{e.target_id}:{e.port}={e.state}" for e in entries) or "no targets"),
                {"targets": [e.model_dump(mode="json") for e in entries]},
            )
        return tuple(out)
