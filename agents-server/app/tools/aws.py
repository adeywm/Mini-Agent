"""AWS tools for one AWS connection.

* ``aws_read`` / ``aws_list_operations``: local boto3 calls through a session that refuses
  every non-READ operation in-process (``security.aws_access``).
* ``aws_change`` (connections at L2 or L3 only): one create/update/delete API call. It runs only
  after the tool guard and the user's approval (``app.agent``), never for reads or secret
  reads, and is never retried by the SDK (a retried create could run twice).
* Managed AWS MCP Server tools (documentation, ``call_aws``), SigV4-signed with the
  connection's profile. ``aws___run_script`` and presigned URLs are never loaded.
"""

from __future__ import annotations

import json
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


class _ListOpsArgs(BaseModel):
    service: str = Field(description="botocore service name, e.g. 'ec2'")
    contains: str | None = Field(default=None, description="filter operation names")


class _ChangeArgs(BaseModel):
    service: str = Field(description="botocore service name, e.g. 'ec2', 'opensearchserverless'")
    operation: str = Field(description="PascalCase API name, e.g. 'DeleteCollection'")
    params: dict[str, Any] = Field(default_factory=dict, description="exact API parameters")
    region: str | None = Field(default=None, description="defaults to the connection region")


def change_tool(conn: Connection) -> BaseTool:
    from botocore.config import Config

    def aws_change(
        service: str,
        operation: str,
        params: dict[str, Any] | None = None,
        region: str | None = None,
    ) -> str:
        access = classify(service, operation)
        if access in (AccessClass.READ, AccessClass.SENSITIVE_READ):
            return f"refused: {service}:{operation} is a read; use aws_read"
        import boto3

        session = boto3.Session(profile_name=conn.aws_profile or None)
        client = session.client(
            service,
            region_name=region or conn.aws_region,
            # Never let the SDK repeat a change on its own; the ledger decides retries.
            config=Config(retries={"total_max_attempts": 1, "mode": "standard"}),
        )
        method = client.meta.method_to_api_mapping
        name = next((m for m, api in method.items() if api == operation), None)
        if name is None:
            return f"unknown operation {service}:{operation}"
        result = getattr(client, name)(**(params or {}))
        return json.dumps(redact(_jsonable(result)), default=str)[:MAX_OUTPUT]

    return StructuredTool.from_function(
        func=aws_change,
        name="aws_change",
        description=(
            "Make one AWS API change (Create*/Update*/Modify*/Put*/Delete*/Terminate*...) with "
            "the connection's credentials. Every call waits for the user's approval card. "
            "Read the current state with aws_read first. Use exact parameters; one resource "
            "per call where possible."
        ),
        args_schema=_ChangeArgs,
    )


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
            func=aws_list_operations,
            name="aws_list_operations",
            description="List an AWS service's API operations and whether each is a read.",
            args_schema=_ListOpsArgs,
        ),
        *([change_tool(conn)] if conn.max_level >= 2 else []),
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
