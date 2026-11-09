"""Provider-neutral operation model.

Every action the platform reasons about, AWS or GitHub, read or write, is an
``OperationRequest``. Nothing in the core branches on a specific AWS service or repository.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AccessClass(StrEnum):
    READ = "READ"  # metadata/config inspection, safe for autonomous use
    SENSITIVE_READ = "SENSITIVE_READ"  # secret values, credentials, data-plane contents
    WRITE = "WRITE"  # anything that may change state
    UNKNOWN = "UNKNOWN"  # never treated as READ


class OperationRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["aws", "github"]
    service: str = Field(min_length=1, max_length=64)
    operation: str = Field(min_length=1, max_length=128)

    def __str__(self) -> str:
        return f"{self.provider}:{self.service}:{self.operation}"


class ResourceScope(BaseModel):
    """Target scope. Resolved and checked server-side; never self-attesting."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    account_alias: str | None = None
    region: str | None = None
    repository: str | None = None
    resource_ids: tuple[str, ...] = ()


class OperationSpec(BaseModel):
    """What discovery knows about an operation (from botocore models or GitHub catalogue)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    ref: OperationRef
    access: AccessClass
    documentation: str = ""
    required_params: tuple[str, ...] = ()
    params: tuple[str, ...] = ()
    paginated: bool = False


class OperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ref: OperationRef
    scope: ResourceScope
    params: dict[str, Any] = Field(default_factory=dict)
    max_pages: int = Field(default=1, ge=1, le=20)


class OperationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ref: OperationRef
    scope: ResourceScope
    evidence_id: str
    pages: int
    truncated: bool
    data: dict[str, Any]


class OperationDenied(PermissionError):
    """Raised when policy/classification refuses an operation. Fail closed."""
