"""Typed, redaction-friendly views of the ECS/ELB data the investigator reads.

Only fields needed for diagnosis are kept. Container environment variables, secrets and
command lines are deliberately never copied out of AWS responses.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ServiceEvent(_Frozen):
    id: str
    created_at: datetime
    message: str


class Deployment(_Frozen):
    id: str
    status: str
    rollout_state: str | None = None
    rollout_state_reason: str | None = None
    task_definition: str
    desired_count: int
    running_count: int
    pending_count: int = 0
    failed_tasks: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ContainerState(_Frozen):
    name: str
    image: str | None = None
    last_status: str | None = None
    exit_code: int | None = None
    reason: str | None = None
    health_status: str | None = None


class StoppedTask(_Frozen):
    task_arn: str
    task_definition: str
    stopped_reason: str | None = None
    stop_code: str | None = None
    stopped_at: datetime | None = None
    containers: tuple[ContainerState, ...] = ()


class TargetHealth(_Frozen):
    target_group_arn: str
    target_id: str
    port: int | None = None
    state: str
    reason: str | None = None
    description: str | None = None


class ServiceSnapshot(_Frozen):
    cluster: str
    name: str
    arn: str
    status: str
    launch_type: str | None = None
    desired_count: int
    running_count: int
    pending_count: int
    task_definition: str
    circuit_breaker_enabled: bool = False
    target_group_arns: tuple[str, ...] = ()
    deployments: tuple[Deployment, ...] = ()
    events: tuple[ServiceEvent, ...] = ()
    stopped_tasks: tuple[StoppedTask, ...] = ()
    target_health: tuple[TargetHealth, ...] = ()
    container_images: tuple[str, ...] = ()


class EcsSnapshot(_Frozen):
    account_alias: str
    region: str
    cluster: str
    cluster_status: str
    collected_at: datetime
    services: tuple[ServiceSnapshot, ...]
    truncated: bool = False
    collection_errors: tuple[str, ...] = ()


class Severity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class FindingCategory(StrEnum):
    SERVICE_UNDERSCALED = "SERVICE_UNDERSCALED"
    DEPLOYMENT_FAILED = "DEPLOYMENT_FAILED"
    DEPLOYMENT_STALLED = "DEPLOYMENT_STALLED"
    IMAGE_PULL_FAILURE = "IMAGE_PULL_FAILURE"
    OUT_OF_MEMORY = "OUT_OF_MEMORY"
    CONTAINER_EXIT = "CONTAINER_EXIT"
    HEALTH_CHECK_FAILURE = "HEALTH_CHECK_FAILURE"
    TARGETS_UNHEALTHY = "TARGETS_UNHEALTHY"
    PLACEMENT_FAILURE = "PLACEMENT_FAILURE"
    TASK_INIT_FAILURE = "TASK_INIT_FAILURE"
    SERVICE_INACTIVE = "SERVICE_INACTIVE"


class Finding(_Frozen):
    id: str
    service: str
    category: FindingCategory
    severity: Severity
    title: str
    detail: str
    evidence_ids: tuple[str, ...] = Field(min_length=1)


class ServiceHealth(_Frozen):
    service: str
    verdict: Literal["HEALTHY", "DEGRADED", "UNHEALTHY"]
    desired_count: int
    running_count: int
    finding_ids: tuple[str, ...] = ()
