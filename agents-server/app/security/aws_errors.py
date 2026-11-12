"""Classify AWS failures without exposing credential or response details."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

from botocore.exceptions import (
    ClientError,
    LoginRefreshRequired,
    SSOTokenLoadError,
    UnauthorizedSSOTokenError,
)

LoginMethod = Literal["login", "sso"]


class AwsToolLoginRequired(Exception):
    """Remember which configured connection encountered expired credentials."""

    def __init__(self, connection: str, profile: str | None, method: LoginMethod) -> None:
        super().__init__("AWS connection sign-in expired")
        self.connection = connection
        self.profile = profile
        self.method = method


def _walk_errors(error: BaseException) -> Iterator[BaseException]:
    pending = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        elif current.__context__ is not None and not current.__suppress_context__:
            pending.append(current.__context__)


def expired_login_method(error: BaseException) -> LoginMethod | None:
    """Find an expired login in wrapped errors, including async exception groups."""
    for current in _walk_errors(error):
        if isinstance(current, AwsToolLoginRequired):
            return current.method
        if isinstance(current, LoginRefreshRequired):
            return "login"
        if isinstance(current, SSOTokenLoadError | UnauthorizedSSOTokenError):
            return "sso"
    return None


def tool_login_context(error: BaseException) -> AwsToolLoginRequired | None:
    return next((e for e in _walk_errors(error) if isinstance(e, AwsToolLoginRequired)), None)


def login_error_type(error: BaseException) -> str:
    """Report the actionable nested error type instead of an exception-group wrapper."""
    for current in _walk_errors(error):
        if isinstance(
            current, LoginRefreshRequired | SSOTokenLoadError | UnauthorizedSSOTokenError
        ):
            return type(current).__name__
    return type(error).__name__


@dataclass(frozen=True)
class AwsServiceError:
    operation: str
    code: str
    cost_history_unavailable: bool
    cost_end_date_invalid: bool
    status: int | None = None  # HTTP status; 5xx or missing means a change's outcome is unknown


def service_error(error: BaseException) -> AwsServiceError | None:
    """Return safe identifiers and recognize the Cost Explorer history error."""
    for current in _walk_errors(error):
        if not isinstance(current, ClientError):
            continue
        raw_error = current.response.get("Error", {})
        code, operation = str(raw_error.get("Code", "")), current.operation_name
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,63}", code):
            code = "UnknownError"
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,63}", operation):
            operation = "AWS operation"
        history = (
            operation == "GetCostAndUsage"
            and code == "ValidationException"
            and "historical data beyond 14 months" in str(raw_error.get("Message", "")).lower()
        )
        end_date = (
            operation == "GetCostAndUsage"
            and code == "ValidationException"
            and "end date past the beginning of next month"
            in str(raw_error.get("Message", "")).lower()
        )
        status = current.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        return AwsServiceError(
            operation, code, history, end_date, status if isinstance(status, int) else None
        )
    return None
