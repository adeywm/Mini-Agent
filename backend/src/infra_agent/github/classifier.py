"""Deterministic access classification for any GitHub REST operation.

A GitHub operation is ``(HTTP method, path)``. Generic across the whole REST API: no
per-endpoint tools. Paths that read secret material are SENSITIVE_READ.
"""

from __future__ import annotations

import re

from infra_agent.core.operations import AccessClass

_SENSITIVE_PATH = re.compile(
    r"/(actions|dependabot|codespaces)/secrets|/keys(/|$)|/installation/token|"
    r"/access_tokens|/credential-authorizations|/hooks/[^/]+/config"
)
_REPO_PATH = re.compile(r"^/repos/([A-Za-z0-9][A-Za-z0-9-]{0,38})/([A-Za-z0-9._-]{1,100})(/|$)")
_SAFE_PATH = re.compile(r"^/[A-Za-z0-9._~/{}:@+-]*$")


def classify_github(method: str, path: str) -> AccessClass:
    method = method.upper()
    if not _SAFE_PATH.match(path) or ".." in path:
        return AccessClass.UNKNOWN
    if method in ("GET", "HEAD"):
        return AccessClass.SENSITIVE_READ if _SENSITIVE_PATH.search(path) else AccessClass.READ
    if method in ("POST", "PUT", "PATCH"):
        return AccessClass.WRITE
    if method == "DELETE":
        return AccessClass.DELETE
    return AccessClass.UNKNOWN


def repo_of(path: str) -> str | None:
    m = _REPO_PATH.match(path)
    return f"{m.group(1)}/{m.group(2)}" if m else None
