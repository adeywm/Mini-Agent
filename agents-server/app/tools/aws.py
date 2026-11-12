"""AWS tools for one AWS connection.

* ``aws_read`` / ``aws_list_operations``: local boto3 calls through a session that refuses
  every non-READ operation in-process (``security.aws_access``).
* ``aws_write`` (L2: create/update) and ``aws_critical`` (L3: delete/terminate and every IAM
  call), always loaded. Each runs one API call, only after the tool guard and the user's
  approval card (``app.agent``), refuses operations of another level, and is never retried
  by the SDK (a retried create could run twice).
* Managed AWS MCP Server tools (documentation, ``call_aws``), SigV4-signed with the
  connection's profile. ``aws___run_script`` and presigned URLs are never loaded.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from app.db import Connection
from app.risk import AWS_MCP_BLOCKED_TOOLS
from app.security.aws_access import AccessClass, classify, read_only_session
from app.security.redaction import redact

MAX_OUTPUT = 30_000


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items() if k != "ResponseMetadata"}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    return value


class _ReadArgs(BaseModel):
    service: str = Field(description="botocore service name, e.g. 'ec2', 's3', 'rds'")
    operation: str = Field(description="PascalCase API name, e.g. 'DescribeInstances'")
    params: dict[str, Any] = Field(default_factory=dict, description="exact API parameters")
    region: str | None = Field(default=None, description="defaults to the connection region")


class _DescribeArgs(BaseModel):
    service: str = Field(description="botocore service name, e.g. 'rds'")
    operation: str = Field(description="PascalCase API name, e.g. 'ModifyDBInstance'")


class _ListOpsArgs(BaseModel):
    service: str = Field(description="botocore service name, e.g. 'ec2'")
    contains: str | None = Field(default=None, description="filter operation names")


class _ChangeArgs(BaseModel):
    service: str = Field(description="botocore service name, e.g. 'ec2', 'opensearchserverless'")
    operation: str = Field(description="PascalCase API name, e.g. 'DeleteCollection'")
    params: dict[str, Any] = Field(default_factory=dict, description="exact API parameters")
    region: str | None = Field(default=None, description="defaults to the connection region")


CHANGE_TOOLS = {
    "aws_write": (
        2,
        "L2: make one AWS create/update API call (RunInstances, ModifyDBInstance, "
        "PutBucketPolicy, ...). Never deletes and never IAM: those use aws_critical.",
    ),
    "aws_critical": (
        3,
        "L3: make one delete/terminate API call, or ANY IAM call (reads included). The "
        "highest-risk tool; the user sees exactly what will happen before it runs.",
    ),
}


def change_tool(conn: Connection, name: str) -> BaseTool:
    """``aws_write`` (L2) or ``aws_critical`` (L3). The tool re-checks in code that the
    operation belongs to its level; it runs only after the guard and the user's approval."""
    from botocore.config import Config

    from app.risk import aws_risk

    level, purpose = CHANGE_TOOLS[name]

    def run(
        service: str,
        operation: str,
        params: dict[str, Any] | None = None,
        region: str | None = None,
    ) -> str:
        risk = aws_risk(name, {"service": service, "operation": operation})
        if risk.level != level:
            return f"refused: {risk.reason}"
        import boto3

        session = boto3.Session(profile_name=conn.aws_profile or None)
        client = session.client(
            service,
            region_name=region or conn.aws_region,
            # Never let the SDK repeat a change on its own; the ledger decides retries.
            config=Config(retries={"total_max_attempts": 1, "mode": "standard"}),
        )
        method = client.meta.method_to_api_mapping
        api = next((m for m, op in method.items() if op == operation), None)
        if api is None:
            return f"unknown operation {service}:{operation}"
        result = getattr(client, api)(**(params or {}))
        return json.dumps(redact(_jsonable(result)), default=str)[:MAX_OUTPUT]

    return StructuredTool.from_function(
        func=run,
        name=name,
        description=(
            f"{purpose} Every call waits for the user's approval card. Read the current "
            "state with aws_read first and get exact parameters with aws_describe_operation. "
            "One resource per call where possible."
        ),
        args_schema=_ChangeArgs,
    )


def _operation_model(service: str, operation: str) -> Any:
    import botocore.session

    return botocore.session.get_session().get_service_model(service).operation_model(operation)


def _shape(shape: Any, depth: int = 0) -> Any:
    """A compact view of a botocore input shape: type names, nested two levels deep."""
    if shape.type_name == "structure" and depth < 2:
        required = set(shape.required_members)
        return {
            name + ("" if name in required else "?"): _shape(member, depth + 1)
            for name, member in shape.members.items()
        }
    if shape.type_name == "list" and depth < 2:
        return [_shape(shape.member, depth + 1)]
    if getattr(shape, "enum", None):
        return " | ".join(shape.enum[:12])
    return shape.type_name


def describe_operation(service: str, operation: str) -> str:
    """Parameters of one AWS operation (``?`` = optional) and its access class."""
    try:
        model = _operation_model(service, operation)
    except Exception as exc:
        return f"unknown operation {service}:{operation} ({type(exc).__name__})"
    doc = re.sub(r"<[^>]+>", "", model.documentation or "").strip().split(". ")[0][:400]
    params = _shape(model.input_shape) if model.input_shape is not None else {}
    return json.dumps(
        {"operation": f"{service}:{operation}", "access": str(classify(service, operation)),
         "summary": doc, "params": params},
        default=str,
    )[:MAX_OUTPUT]  # fmt: skip


def invalid_params(service: str, operation: str, params: Any) -> str | None:
    """Why these parameters would be rejected by the AWS API model, or None if valid.
    Checked before the approval card, so the user never approves a call that cannot run."""
    from botocore.validate import ParamValidator

    try:
        model = _operation_model(service, operation)
    except Exception:
        return f"unknown operation {service}:{operation}"
    if model.input_shape is None:
        return None if not params else f"{service}:{operation} takes no parameters"
    report = ParamValidator().validate(params or {}, model.input_shape)
    return report.generate_report()[:2000] if report.has_errors() else None


def local_tools(conn: Connection) -> list[BaseTool]:
    import botocore.session

    def aws_read(
        service: str,
        operation: str,
        params: dict[str, Any] | None = None,
        region: str | None = None,
    ) -> str:
        if classify(service, operation) is not AccessClass.READ:
            return f"refused: {service}:{operation} is not a plain read"
        session = read_only_session(conn.aws_profile, region or conn.aws_region)
        client = session.client(service)
        method = client.meta.method_to_api_mapping
        name = next((m for m, api in method.items() if api == operation), None)
        if name is None:
            return f"unknown operation {service}:{operation}"
        result = getattr(client, name)(**(params or {}))
        return json.dumps(redact(_jsonable(result)), default=str)[:MAX_OUTPUT]

    def aws_list_operations(service: str, contains: str | None = None) -> str:
        model = botocore.session.get_session().get_service_model(service)
        ops = [
            f"{op} ({classify(service, op)})"
            for op in model.operation_names
            if not contains or contains.lower() in op.lower()
        ]
        return "\n".join(ops)[:MAX_OUTPUT] or "no matching operations"

    return [
        StructuredTool.from_function(
            func=aws_read,
            name="aws_read",
            description=(
                "Call one read-only AWS API (Describe*/List*/Get*) with the connection's "
                "credentials and region. Writes and secret reads are refused."
            ),
            args_schema=_ReadArgs,
        ),
        StructuredTool.from_function(
            func=describe_operation,
            name="aws_describe_operation",
            description=(
                "Show one AWS operation's parameters (name? = optional, nested types, enums) "
                "and whether it is a read. Use before aws_read, aws_write or aws_critical "
                "for exact parameter names."
            ),
            args_schema=_DescribeArgs,
        ),
        StructuredTool.from_function(
            func=aws_list_operations,
            name="aws_list_operations",
            description="List an AWS service's API operations and whether each is a read.",
            args_schema=_ListOpsArgs,
        ),
        # Always available: every call waits for an approval card, which also elevates
        # the connection for the current request when the change is above its default level.
        *(change_tool(conn, name) for name in CHANGE_TOOLS),
    ]


@asynccontextmanager
async def mcp_tools(
    conn: Connection, endpoint: str, endpoint_region: str
) -> AsyncIterator[list[BaseTool]]:
    import boto3
    from langchain_mcp_adapters.tools import load_mcp_tools
    from mcp import ClientSession
    from mcp_proxy_for_aws.client import aws_iam_streamablehttp_client

    credentials = boto3.Session(profile_name=conn.aws_profile or None).get_credentials()
    if credentials is None:
        raise PermissionError(f"no AWS credentials for profile {conn.aws_profile or 'default'!r}")
    async with (
        aws_iam_streamablehttp_client(
            endpoint=endpoint,
            aws_service="aws-mcp",
            aws_region=endpoint_region,
            credentials=credentials.get_frozen_credentials(),
            timeout=timedelta(seconds=120),
        ) as (read, write, *_),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        tools = await load_mcp_tools(session, server_name=f"aws:{conn.name}")
        yield [t for t in tools if t.name not in AWS_MCP_BLOCKED_TOOLS]
