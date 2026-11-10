"""Tools from the official GitHub MCP Server (remote) for one GitHub connection.

The token comes from the GitHub CLI at call time (`gh auth token --user <user>`); it is
never stored. A connection capped at L1 also asks the server for read-only tools only.
"""

from __future__ import annotations

import shutil
import subprocess  # noqa: S404 - fixed argv, no shell

from langchain_core.tools import BaseTool

from app.db import Connection


def github_token(user: str) -> str:
    gh = shutil.which("gh")
    if gh is None:
        raise PermissionError("GitHub CLI `gh` not found; install it and run `gh auth login`")
    result = subprocess.run(  # noqa: S603
        [gh, "auth", "token", "--hostname", "github.com", "--user", user],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    token = result.stdout.strip()
    if result.returncode != 0 or not token:
        raise PermissionError(f"no GitHub login for {user!r}; run `gh auth login`")
    return token


async def mcp_tools(conn: Connection, url: str) -> list[BaseTool]:
    from langchain_mcp_adapters.tools import load_mcp_tools

    assert conn.github_user
    headers = {"Authorization": f"Bearer {github_token(conn.github_user)}"}
    if conn.max_level == 1:
        headers["X-MCP-Readonly"] = "true"
    return await load_mcp_tools(
        None,
        connection={"transport": "streamable_http", "url": url, "headers": headers},
        server_name=f"github:{conn.name}",
    )
