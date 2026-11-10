"""Platform permission levels (separate from risk/approval policy).

Level 1 READ, Level 2 CREATE/UPDATE, Level 3 DELETE. A level says which *category* of
operation a principal/agent/account may request at all. Whether a permitted operation also
needs human approval is a separate risk-policy decision (Phase 5), and IAM / GitHub App
permissions remain the authoritative boundary. Approval never overrides a missing level.

SENSITIVE_READ (secret values, credentials, data-plane contents) is not granted by any
level; it needs an explicit, operation-specific grant. UNKNOWN is never permitted.
"""

from __future__ import annotations

from enum import IntEnum

from infra_agent.core.operations import AccessClass


class PermissionLevel(IntEnum):
    READ = 1
    WRITE = 2
    DELETE = 3


_REQUIRED: dict[AccessClass, PermissionLevel] = {
    AccessClass.READ: PermissionLevel.READ,
    AccessClass.WRITE: PermissionLevel.WRITE,
    AccessClass.DELETE: PermissionLevel.DELETE,
}


def required_level(access: AccessClass) -> PermissionLevel | None:
    """Level an operation needs, or None if no level can grant it."""
    return _REQUIRED.get(access)


def has_permission(granted: PermissionLevel, access: AccessClass) -> bool:
    needed = required_level(access)
    return needed is not None and granted >= needed
