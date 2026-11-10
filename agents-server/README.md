# agents-server

Backend for Mini-Agent: a personal chat assistant that uses your GitHub and AWS accounts
through MCP tools and asks you before risky actions.

```bash
cp .env.example .env      # set the Bedrock model and region
uv sync
uv run server.py          # http://127.0.0.1:8000
uv run pytest
```

The server binds to localhost only. Connections (GitHub account, AWS profile and region,
maximum permission level) are added from the app; credentials are never stored, only the
`gh` user name or AWS profile name that points to them.
