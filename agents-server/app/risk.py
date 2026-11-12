"""Risk level of every tool call, decided in code (never by the model).

Levels: 1 = read (runs automatically), 2 = create/update and 3 = delete or any AWS IAM
operation (both need the user's approval). Anything that cannot be classified with confidence
is treated as at least level 2, so it never runs without approval. Some tools are blocked
outright (``level is None``): secret reads, arbitrary scripts, presigned URLs.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Any

from app.security.aws_access import AccessClass, classify

READ, WRITE, DELETE = 1, 2, 3
LEVEL_NAMES = {READ: "L1 read", WRITE: "L2 create/update", DELETE: "L3 delete/IAM"}
# Every operation on these services is L3, reads included (identity and access).
IAM_SERVICES = frozenset({"iam"})


@dataclass(frozen=True)
class Risk:
    level: int | None  # None = blocked, never runs
    reason: str

    @property
    def blocked(self) -> bool:
        return self.level is None


_GH_READ_PREFIXES = ("get_", "list_", "search_", "download_")
_GH_DELETE_PREFIXES = ("delete_", "remove_")
_GH_WRITE_PREFIXES = (
    "create_", "update_", "add_", "push_", "merge_", "fork_", "assign_", "request_",
    "run_", "rerun_", "cancel_", "dismiss_", "mark_", "manage_", "star_", "unstar_",
    "submit_", "resolve_", "reprioritize_", "set_",
)  # fmt: skip


def github_risk(tool: str, args: dict[str, Any]) -> Risk:
    method = str(args.get("method", "")).lower()
    if tool.startswith(_GH_DELETE_PREFIXES) or (tool.endswith("_write") and "delete" in method):
        return Risk(DELETE, "deletes GitHub data")
    if tool.startswith(_GH_READ_PREFIXES) or tool.endswith("_read") or tool == "get_me":
        return Risk(READ, "reads GitHub data")
    if tool.startswith(_GH_WRITE_PREFIXES) or tool.endswith("_write"):
        return Risk(WRITE, "changes GitHub data")
    return Risk(WRITE, "unrecognised GitHub tool; treated as a change")


AWS_MCP_READ_TOOLS = frozenset(
    {
        "aws___search_documentation",
        "aws___read_documentation",
        "aws___recommend",
        "aws___retrieve_skill",
        "aws___list_regions",
        "aws___get_regional_availability",
        "aws___suggest_aws_commands",
        "aws___get_tasks",  # status of the server's own background tasks
    }
)
# Arbitrary scripts cannot be inspected before they run; presigned URLs hand out data access.
AWS_MCP_BLOCKED_TOOLS = frozenset({"aws___run_script", "aws___get_presigned_url"})

# AWS CLI command names that differ from the botocore service name.
_CLI_SERVICE = {"s3api": "s3", "configservice": "config"}
# High-level `aws s3` commands.
_S3_HIGH_LEVEL = {"ls": READ, "presign": None, "cp": WRITE, "mv": WRITE, "sync": WRITE,
                  "mb": WRITE, "website": WRITE, "rm": DELETE, "rb": DELETE}  # fmt: skip


def _access_to_risk(access: AccessClass, what: str, service: str) -> Risk:
    if access is AccessClass.SENSITIVE_READ:
        return Risk(None, f"{what} returns secret material")
    if service in IAM_SERVICES:
        return Risk(DELETE, f"IAM operation {what}")
    if access is AccessClass.READ:
        return Risk(READ, f"reads {what}")
    if access is AccessClass.DELETE:
        return Risk(DELETE, f"deletes with {what}")
    if access is AccessClass.WRITE:
        return Risk(WRITE, f"changes AWS with {what}")
    return Risk(WRITE, f"unrecognised AWS operation {what}")


def aws_cli_risk(command: str) -> Risk:
    """Risk of one `aws <service> <operation> ...` CLI command (AWS MCP ``call_aws``)."""
    try:
        parts = shlex.split(command)
    except ValueError:
        return Risk(None, "could not parse the AWS CLI command")
    if parts[:1] == ["aws"]:
        parts = parts[1:]
    if any(sep in command for sep in ("|", ";", "&&", "`", "$(")) or len(parts) < 2:
        return Risk(None, "only a single plain `aws <service> <operation>` command is allowed")
    service, operation = parts[0], parts[1]
    if service == "s3":
        level = _S3_HIGH_LEVEL.get(operation)
        if level is None:
            return Risk(None, f"`aws s3 {operation}` is not allowed")
        return Risk(level, f"`aws s3 {operation}`")
    pascal = "".join(w.capitalize() for w in operation.split("-"))
    service = _CLI_SERVICE.get(service, service)
    return _access_to_risk(classify(service, pascal), f"{service}:{pascal}", service)


def aws_risk(tool: str, args: dict[str, Any]) -> Risk:
    if tool in AWS_MCP_BLOCKED_TOOLS:
        return Risk(None, f"{tool} is disabled: it cannot be checked before it runs")
    if tool in AWS_MCP_READ_TOOLS or tool in {"aws_list_operations", "aws_describe_operation"}:
        return Risk(READ, "reads AWS documentation or metadata")
    if tool == "aws_read":
        service, operation = str(args.get("service", "")), str(args.get("operation", ""))
        access = classify(service, operation)
        if access not in (AccessClass.READ, AccessClass.SENSITIVE_READ):
            return Risk(None, f"aws_read only runs reads; {service}:{operation} is a change")
        if service in IAM_SERVICES and access is AccessClass.READ:
            return Risk(None, f"IAM calls are L3; use aws_critical for {service}:{operation}")
        return _access_to_risk(access, f"{service}:{operation}", service)
    if tool in ("aws_write", "aws_critical"):
        service, operation = str(args.get("service", "")), str(args.get("operation", ""))
        risk = _access_to_risk(classify(service, operation), f"{service}:{operation}", service)
        if risk.blocked:
            return risk
        if risk.level == READ:
            return Risk(None, f"{service}:{operation} is a read; use aws_read")
        right = "aws_write" if risk.level == WRITE else "aws_critical"
        if right != tool:
            return Risk(None, f"{service}:{operation} is {LEVEL_NAMES[risk.level]}; use {right}")
        return risk
    if tool == "aws___call_aws":
        return aws_cli_risk(str(args.get("cli_command", args.get("command", ""))))
    return Risk(WRITE, f"unrecognised AWS tool {tool}; treated as a change")


# Plain-language warnings for the approval card, matched on the AWS operation or GitHub tool.
_WARNINGS: tuple[tuple[str, str], ...] = (
    (
        r"(terminate|delete|remove|purge|destroy|deregister|\bs3 rm\b|\bs3 rb\b)",
        "Deletes data or resources; this usually cannot be undone.",
    ),
    (
        r"(stop-|reboot|modify-db|modify-instance|resize|update-service|restart)",
        "May cause downtime while the resource restarts or is modified.",
    ),
    (
        r"(run-instances|create-db|create-cluster|create-nat|allocate-address"
        r"|create-load-balancer|purchase|create-domain|start-)",
        "May start billing for new or running resources.",
    ),
    (r"\biam\b", "Changes or reveals who can access your AWS account."),
    (
        r"(merge_pull_request|push_files|create_or_update_file|delete_file)",
        "Changes repository contents.",
    ),
)


def warnings(tool: str, args: dict[str, Any]) -> list[str]:
    """Cost, downtime and irreversibility hints for one tool call (best effort)."""
    parts = (tool, args.get("cli_command", ""), args.get("service", ""), args.get("operation", ""))
    text = re.sub(r"([a-z])([A-Z])", r"\1-\2", " ".join(str(p) for p in parts)).lower()
    return [message for pattern, message in _WARNINGS if re.search(pattern, text)]
