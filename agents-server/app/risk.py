"""Risk level of every tool call, decided in code (never by the model).

Levels: 1 = read, 2 = create/update, 3 = delete. Anything that cannot be classified with
confidence is treated as at least level 2, so it is never run without the user's approval.
Some tools are blocked outright (``level is None``).
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Any

from app.security.aws_access import AccessClass, classify

READ, WRITE, DELETE = 1, 2, 3
LEVEL_NAMES = {READ: "L1 read", WRITE: "L2 create/update", DELETE: "L3 delete"}


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


def _access_to_risk(access: AccessClass, what: str) -> Risk:
    if access is AccessClass.READ:
        return Risk(READ, f"reads {what}")
    if access is AccessClass.DELETE:
        return Risk(DELETE, f"deletes with {what}")
    if access is AccessClass.SENSITIVE_READ:
        return Risk(WRITE, f"{what} returns secret material")
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
    name = f"{_CLI_SERVICE.get(service, service)}:{pascal}"
    return _access_to_risk(classify(_CLI_SERVICE.get(service, service), pascal), name)


def aws_risk(tool: str, args: dict[str, Any]) -> Risk:
    if tool in AWS_MCP_BLOCKED_TOOLS:
        return Risk(None, f"{tool} is disabled: it cannot be checked before it runs")
    if tool in AWS_MCP_READ_TOOLS or tool in {"aws_list_operations"}:
        return Risk(READ, "reads AWS documentation or metadata")
    if tool == "aws_read":
        service, operation = str(args.get("service", "")), str(args.get("operation", ""))
        return _access_to_risk(classify(service, operation), f"{service}:{operation}")
    if tool == "aws___call_aws":
        return aws_cli_risk(str(args.get("cli_command", args.get("command", ""))))
    return Risk(WRITE, f"unrecognised AWS tool {tool}; treated as a change")
