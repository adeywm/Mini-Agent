from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from mcp.types import CallToolResult, TextContent

from infra_agent.config import AgentConfig
from infra_agent.integrations.aws_mcp import (
    McpGuard,
    McpToolDenied,
    filter_tools,
    open_aws_mcp_tools,
)
from infra_agent.security.credentials import IdentityCheck

AWS_KEY = "AKIA" + "IOSFODNN7EXAMPLE"


@dataclass
class _Tool:
    name: str


@dataclass
class _Req:
    name: str
    args: dict[str, Any]


def test_tool_allowlist_filters_unknown_and_presigned() -> None:
    tools = [
        _Tool("aws___run_script"),
        _Tool("aws___search_documentation"),
        _Tool("aws___get_presigned_url"),
        _Tool("aws___call_aws"),
        _Tool("aws___brand_new_write_tool"),
    ]
    kept, dropped = filter_tools(tools)
    assert [t.name for t in kept] == ["aws___run_script", "aws___search_documentation"]
    assert dropped == ["aws___brand_new_write_tool", "aws___call_aws", "aws___get_presigned_url"]


async def test_guard_denies_non_allowlisted_call_and_audits() -> None:
    guard = McpGuard(correlation_id="t1")
    called = False

    async def handler(_: Any) -> Any:
        nonlocal called
        called = True

    with pytest.raises(McpToolDenied):
        await guard(_Req("aws___get_presigned_url", {"bucket": "b"}), handler)
    assert not called
    assert guard.audit[0].outcome == "denied"


async def test_guard_redacts_tool_output() -> None:
    guard = McpGuard(correlation_id="t1")

    async def handler(_: Any) -> CallToolResult:
        return CallToolResult(
            content=[TextContent(type="text", text=f"found key {AWS_KEY} password=hunter2x")],
            structuredContent={"raw": AWS_KEY},
        )

    result = await guard(_Req("aws___run_script", {"script": "..."}), handler)
    text = result.content[0].text
    assert AWS_KEY not in text and "hunter2x" not in text
    assert result.structuredContent is None
    assert guard.audit[0].outcome == "allowed"


async def test_mcp_refuses_unvalidated_identity(cfg: AgentConfig) -> None:
    bad = IdentityCheck(account_alias="dev", status="failed")
    with pytest.raises(PermissionError):
        async with open_aws_mcp_tools(cfg.aws_mcp, bad, None, "ap-south-1", "t1"):
            pass
