"""Managed AWS MCP Server adapter (read-only discovery path).

Security model (ADR-002):

* The connection is SigV4-signed with the *validated read-only* credentials. IAM is the
  boundary: ``aws___run_script`` can run arbitrary Python, so it must never be reachable with
  a write-capable principal. ``open_aws_mcp_tools`` refuses to connect without a passed
  ``IdentityCheck``.
* Tools are filtered to an explicit allowlist; unknown or new server tools are dropped.
* An interceptor re-checks the tool name on every call and redacts tool output before it
  reaches model context, and records an audit trail of calls.

The tool contract (argument names of ``aws___run_script`` etc.) is not published by AWS, so
the schema is taken from the server's own ``tools/list`` at connect time.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from mcp import ClientSession
from mcp.types import CallToolResult, TextContent

from infra_agent.config import AwsMcpConfig
from infra_agent.models.domain import AuditEvent, sha256_digest
from infra_agent.security.credentials import IdentityCheck
from infra_agent.security.redaction import redact_text

ALLOWED_MCP_TOOLS: frozenset[str] = frozenset(
    {
        "aws___search_documentation",
        "aws___read_documentation",
        "aws___retrieve_skill",
        "aws___list_regions",
        "aws___get_regional_availability",
        "aws___run_script",
        "aws___get_tasks",
    }
)
# Explicitly excluded even though read-ish: presigned URLs hand out data-access capability.
DENIED_MCP_TOOLS: frozenset[str] = frozenset({"aws___get_presigned_url", "aws___call_aws"})


class McpToolDenied(PermissionError):
    pass


@dataclass
class McpGuard:
    correlation_id: str
    audit: list[AuditEvent] = field(default_factory=list)

    async def __call__(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        name = str(request.name)
        args_digest = sha256_digest(request.args)
        if name in DENIED_MCP_TOOLS or name not in ALLOWED_MCP_TOOLS:
            self.audit.append(AuditEvent(actor="agent", action=f"mcp:{name}", target="aws-mcp",
                                         outcome="denied", correlation_id=self.correlation_id,
                                         payload_digest=args_digest))
            raise McpToolDenied(f"MCP tool {name} is not allowlisted")
        result = await handler(request)
        self.audit.append(AuditEvent(actor="agent", action=f"mcp:{name}", target="aws-mcp",
                                     outcome="allowed", correlation_id=self.correlation_id,
                                     payload_digest=args_digest))
        return redact_result(result)


def redact_result(result: Any) -> Any:
    if isinstance(result, CallToolResult):
        content = [
            TextContent(type="text", text=redact_text(c.text)) if isinstance(c, TextContent) else c
            for c in result.content
        ]
        return result.model_copy(update={"content": content, "structuredContent": None})
    return result


def filter_tools(tools: list[Any]) -> tuple[list[Any], list[str]]:
    kept = [t for t in tools if t.name in ALLOWED_MCP_TOOLS and t.name not in DENIED_MCP_TOOLS]
    dropped = sorted(t.name for t in tools if t not in kept)
    return kept, dropped


@dataclass
class AwsMcpTools:
    tools: list[Any]
    dropped: list[str]
    guard: McpGuard


@asynccontextmanager
async def open_aws_mcp_tools(
    cfg: AwsMcpConfig,
    identity: IdentityCheck,
    credentials: Any,
    target_region: str,
    correlation_id: str,
) -> AsyncIterator[AwsMcpTools]:
    if not identity.ok:
        raise PermissionError("AWS MCP requires a validated read-only identity")

    # Imported lazily so mock/test runs never load network clients.
    from langchain_mcp_adapters.tools import load_mcp_tools
    from mcp_proxy_for_aws.client import aws_iam_streamablehttp_client

    guard = McpGuard(correlation_id=correlation_id)
    async with (
        aws_iam_streamablehttp_client(
            endpoint=cfg.endpoint,
            aws_service=cfg.sigv4_service,
            aws_region=cfg.endpoint_region,
            credentials=credentials,
            metadata={"AWS_REGION": target_region},
            timeout=timedelta(seconds=cfg.timeout_seconds),
        ) as (read, write, *_),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        tools = await load_mcp_tools(session, tool_interceptors=[guard], server_name="aws-mcp")
        kept, dropped = filter_tools(tools)
        yield AwsMcpTools(tools=kept, dropped=dropped, guard=guard)
