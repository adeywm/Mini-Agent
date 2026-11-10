# agents-server

Backend for Mini-Agent: a FastAPI app that runs one LangChain agent (Claude on Amazon Bedrock)
over the GitHub and AWS tools of your saved connections. See the [main README](../README.md)
for features, the safety model and setup.

```bash
cp .env.example .env      # optional settings (MINI_AGENT_*)
uv sync
uv run server.py          # http://127.0.0.1:8000, localhost only
uv run pytest             # tests; no network or credentials needed
uv run ruff check . && uv run ruff format --check .
```
