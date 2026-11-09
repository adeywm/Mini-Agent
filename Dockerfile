# infra-agent container: API, worker, events poller and AgentCore entrypoint share one image.
# AgentCore Runtime requires linux/arm64 and port 8080:
#   docker buildx build --platform linux/arm64 -t infra-agent .
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=ghcr.io/astral-sh/uv:0.11.8 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev

# Non-root runtime user; config is mounted read-only at /app/config/agent.toml.
RUN useradd --uid 10001 --create-home agent
USER agent

ENV PATH="/app/.venv/bin:$PATH" \
    INFRA_AGENT_CONFIG_PATH=/app/config/agent.toml

EXPOSE 8080
# Default: AgentCore entrypoint. Override CMD for other roles:
#   ["infra-agent", "worker"]  |  ["infra-agent", "events"]
#   ["uvicorn", "infra_agent.api.main:app", "--host", "0.0.0.0", "--port", "8080"]
CMD ["uvicorn", "infra_agent.agentcore_app:app", "--host", "0.0.0.0", "--port", "8080"]
