"""Minimal FastAPI app (Phase 1): health endpoints only.

Task, approval and webhook routes arrive with authentication in later phases; nothing here
can trigger AWS or GitHub calls.
"""

from __future__ import annotations

from fastapi import FastAPI, Response

from infra_agent.config import AppSettings, ConfigError, load_agent_config

app = FastAPI(title="infra-agent", version="0.1.0")


@app.get("/health/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
def ready(response: Response) -> dict[str, str]:
    try:
        settings = AppSettings()
        load_agent_config(settings.config_path)
    except (ConfigError, ValueError) as exc:
        response.status_code = 503
        # Only the error class is returned; details stay in server logs.
        return {"status": "not_ready", "reason": type(exc).__name__}
    return {"status": "ready", "writes": "disabled"}
