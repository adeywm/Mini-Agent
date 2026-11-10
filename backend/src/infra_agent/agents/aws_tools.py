"""LangChain tools that let a model explore *any* AWS service read-only.

Structured alternative to free-form scripts: the model names a service/operation and
parameters; ``AwsReader`` validates them against botocore models, enforces READ-only
classification and operator scope, and returns redacted results.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from infra_agent.aws import catalog
from infra_agent.aws.reader import AwsReader
from infra_agent.core.operations import (
    AccessClass,
    OperationRef,
    OperationRequest,
    ResourceScope,
)

_MAX_TOOL_CHARS = 20_000


class _ListOpsArgs(BaseModel):
    service: str = Field(description="botocore service name, e.g. ec2, ecs, s3, rds, logs")
    contains: str | None = Field(default=None, description="Optional substring filter")


class _ReadArgs(BaseModel):
    service: str = Field(description="botocore service name, e.g. ec2, ecs, elbv2, logs")
    operation: str = Field(description="API operation in PascalCase, e.g. DescribeInstances")
    region: str = Field(description="Target AWS region (must be allowed by operator config)")
    params: dict[str, Any] = Field(default_factory=dict, description="API parameters")
    max_pages: int = Field(default=1, ge=1, le=5)


def make_aws_tools(reader: AwsReader, account_alias: str) -> list[BaseTool]:
    def list_read_operations(service: str, contains: str | None = None) -> str:
        try:
            specs = catalog.list_operations(service, AccessClass.READ)
        except Exception as exc:
            return f"error: {exc}"
        names = [s.ref.operation for s in specs if not contains or contains in s.ref.operation]
        return json.dumps(names[:300])

    def describe_operation(service: str, contains: str | None = None) -> str:
        if not contains:
            return "error: pass the exact operation name in 'contains'"
        try:
            return catalog.describe(service, contains).model_dump_json()
        except Exception as exc:
            return f"error: {exc}"

    def aws_read(
        service: str,
        operation: str,
        region: str,
        params: dict[str, Any] | None = None,
        max_pages: int = 1,
    ) -> str:
        request = OperationRequest(
            ref=OperationRef(provider="aws", service=service, operation=operation),
            scope=ResourceScope(account_alias=account_alias, region=region),
            params=params or {},
            max_pages=max_pages,
        )
        try:
            result = reader.call(request)
        except Exception as exc:
            return f"refused or failed: {type(exc).__name__}: {exc}"
        text = json.dumps({"evidence_id": result.evidence_id, "data": result.data}, default=str)
        return text[:_MAX_TOOL_CHARS]

    return [
        StructuredTool.from_function(
            func=list_read_operations,
            name="aws_list_read_operations",
            description="List READ-only API operations available for an AWS service.",
            args_schema=_ListOpsArgs,
        ),
        StructuredTool.from_function(
            func=describe_operation,
            name="aws_describe_operation",
            description="Show parameters and docs of one AWS operation (name in 'contains').",
            args_schema=_ListOpsArgs,
        ),
        StructuredTool.from_function(
            func=aws_read,
            name="aws_read",
            description=(
                "Call one READ-only AWS API operation in the configured account. "
                "Writes and secret reads are refused."
            ),
            args_schema=_ReadArgs,
        ),
    ]
