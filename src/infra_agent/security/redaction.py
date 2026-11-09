"""Mask secrets before data reaches model context, reports or logs.

Applied to every AWS/GitHub/MCP payload. Pattern-based redaction is a safety net, not a
guarantee; the collectors also avoid reading secret-bearing fields in the first place.
"""

from __future__ import annotations

import re
from typing import Any

REDACTED = "[REDACTED]"

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # AWS access key IDs (long-term AKIA, temporary ASIA, etc.)
    (re.compile(r"\b(?:AKIA|ASIA|ABIA|ACCA|AGPA|AIDA|AROA|ANPA|ANVA|AIPA)[A-Z0-9]{16}\b"), REDACTED),
    # GitHub tokens
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,255}|github_pat_[A-Za-z0-9_]{20,255})\b"), REDACTED),
    # Slack tokens
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), REDACTED),
    # JWTs
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), REDACTED),
    # PEM private keys
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
        REDACTED,
    ),
    # Authorization headers
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 " + REDACTED),
    # key=value / key: value secrets (password, secret, token, api key, aws_secret_access_key)
    (
        re.compile(
            r"(?i)\b([A-Za-z0-9_-]*(?:password|passwd|pwd|secret|token|api[_-]?key|"
            r"access[_-]?key|session[_-]?key|private[_-]?key)[A-Za-z0-9_-]*)"
            r"(\s*[:=]\s*[\"']?)([^\s\"',;}]{4,})"
        ),
        r"\1\2" + REDACTED,
    ),
    # Connection strings with inline credentials
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^\s:/@]+:[^\s@/]+@"), r"\1" + REDACTED + "@"),
)

_SENSITIVE_KEYS = re.compile(
    r"(?i)^(password|secret|secretstring|secretbinary|token|sessiontoken|secretaccesskey|"
    r"accesskeyid|privatekey|credentials|authorization|environment|secrets)$"
)


def redact_text(text: str) -> str:
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


def redact(value: Any) -> Any:
    """Recursively redact strings; drop values under secret-bearing keys entirely."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for k, v in value.items():
            if isinstance(k, str) and _SENSITIVE_KEYS.match(k):
                out[k] = REDACTED
            else:
                out[k] = redact(v)
        return out
    if isinstance(value, list | tuple):
        return type(value)(redact(v) for v in value)
    return value


def mask_account_ids(text: str) -> str:
    """Partially mask 12-digit account IDs in ARNs for reports shared outside the operator."""
    return re.sub(r"(?<=:)(\d{4})\d{4}(\d{4})(?=:)", r"\1****\2", text)
