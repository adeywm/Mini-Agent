"""AWS sign-in diagnostics do not require live cloud credentials."""

from contextlib import asynccontextmanager
from typing import Any

import pytest
from botocore.exceptions import ClientError, LoginRefreshRequired, UnauthorizedSSOTokenError

from app import agent
from app.db import Connection
from app.security.aws_errors import (
    AwsToolLoginRequired,
    expired_login_method,
    login_error_type,
    service_error,
    tool_login_context,
)
from app.settings import Settings


def test_expired_login_types_and_wrapped_errors() -> None:
    assert expired_login_method(LoginRefreshRequired()) == "login"
    assert expired_login_method(UnauthorizedSSOTokenError()) == "sso"
    wrapped = ExceptionGroup("tool errors", [LoginRefreshRequired()])
    assert expired_login_method(wrapped) == "login"
    assert login_error_type(wrapped) == "LoginRefreshRequired"
    denied = ClientError({"Error": {"Code": "AccessDeniedException", "Message": "denied"}}, "Read")
    assert expired_login_method(denied) is None


def test_nested_cost_explorer_error_reports_safe_details() -> None:
    error = ClientError(
        {
            "Error": {
                "Code": "ValidationException",
                "Message": (
                    "You haven't enabled historical data beyond 14 months. sensitive-example-value"
                ),
            }
        },
        "GetCostAndUsage",
    )
    failure = service_error(ExceptionGroup("tools", [ExceptionGroup("mcp", [error])]))
    assert failure is not None
    assert (failure.operation, failure.code, failure.cost_history_unavailable) == (
        "GetCostAndUsage",
        "ValidationException",
        True,
    )
    assert "sensitive-example-value" not in repr(failure)


def test_cost_end_date_error_is_classified_without_raw_message() -> None:
    error = ClientError(
        {
            "Error": {
                "Code": "ValidationException",
                "Message": "end date past the beginning of next month sensitive-example-value",
            }
        },
        "GetCostAndUsage",
    )
    failure = service_error(ExceptionGroup("wrapped", [error]))
    assert failure is not None and failure.cost_end_date_invalid
    assert "sensitive-example-value" not in repr(failure)


def test_tool_login_context_survives_nested_exception_groups() -> None:
    original = AwsToolLoginRequired("personal", "work", "login")
    wrapped = ExceptionGroup("tools", [ExceptionGroup("mcp", [original])])
    assert expired_login_method(wrapped) == "login"
    assert tool_login_context(wrapped) is original


@pytest.mark.asyncio
async def test_tool_failure_keeps_connection_profile() -> None:
    from langchain.agents.middleware.types import ToolCallRequest

    conn = Connection(
        id="c",
        created_at="now",
        kind="aws",
        name="personal",
        max_level=1,
        aws_profile="work",
        aws_region="ap-south-1",
    )
    guard = agent.ToolGuard({"personal__aws_read": agent.ToolInfo(conn, "aws_read")})
    request = ToolCallRequest(
        tool_call={
            "name": "personal__aws_read",
            "id": "call-1",
            "args": {"service": "ec2", "operation": "DescribeInstances"},
        },
        tool=None,
        state={},
        runtime=None,  # type: ignore[arg-type]
    )

    async def expired(_request: ToolCallRequest) -> Any:
        raise LoginRefreshRequired()

    with pytest.raises(AwsToolLoginRequired) as caught:
        await guard.awrap_tool_call(request, expired)
    assert (caught.value.connection, caught.value.profile, caught.value.method) == (
        "personal",
        "work",
        "login",
    )


@pytest.mark.asyncio
async def test_expired_connection_reports_profile_without_raw_exception(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    @asynccontextmanager
    async def unavailable(*_args: Any) -> Any:
        try:
            raise RuntimeError("sensitive-example-value")
        except RuntimeError as exc:
            raise LoginRefreshRequired() from exc
        yield []

    monkeypatch.setattr(agent.aws, "local_tools", lambda _: [])
    monkeypatch.setattr(agent.aws, "mcp_tools", unavailable)
    conn = Connection(
        id="c",
        created_at="now",
        kind="aws",
        name="personal",
        max_level=1,
        aws_profile="work",
        aws_region="ap-south-1",
    )
    async with agent.open_toolset([conn], Settings()) as tools:
        assert len(tools.problems) == 1
        problem = tools.problems[0]
        assert (problem.connection, problem.profile, problem.login_method) == (
            "personal",
            "work",
            "login",
        )
        assert "sensitive-example-value" not in problem.summary
    assert "sensitive-example-value" not in caplog.text


def test_root_cause_unwraps_exception_groups() -> None:
    from app.security.aws_errors import root_cause

    inner = TimeoutError("The read operation timed out")
    try:
        try:
            raise inner
        except TimeoutError as exc:
            raise RuntimeError("wrapped") from exc
    except RuntimeError as wrapped:
        group = BaseExceptionGroup("unhandled errors in a TaskGroup", [wrapped])
    assert root_cause(group) is inner
    plain = ValueError("x")
    assert root_cause(plain) is plain
