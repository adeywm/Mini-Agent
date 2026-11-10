"""Start the local API and its worker."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import uvicorn

from infra_agent.config import AppSettings, ConfigError, load_agent_config
from infra_agent.runtime import Platform


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    os.chdir(root)
    settings = AppSettings()
    try:
        config = load_agent_config(settings.config_path)
        if not settings.database_url:
            raise ConfigError("Set INFRA_AGENT_DATABASE_URL to your PostgreSQL connection URL")
        Platform.from_settings(config, settings)
    except (ConfigError, ValueError) as exc:
        print(f"Cannot start: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(
            f"Cannot connect to or initialize PostgreSQL ({type(exc).__name__}).", file=sys.stderr
        )
        return 1

    worker = subprocess.Popen(
        [sys.executable, "-m", "infra_agent.cli", "worker"],
        cwd=root,
    )
    try:
        uvicorn.run("infra_agent.api.main:app", host="127.0.0.1", port=8080)
    finally:
        worker.terminate()
        worker.wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
