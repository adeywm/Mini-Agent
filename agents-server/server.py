"""Start the Mini-Agent API on localhost: `uv run server.py`.

Restarts on code changes in app/ by default (MINI_AGENT_RELOAD=false to turn off).
"""

import uvicorn

from app.settings import get_settings


def main() -> None:
    settings = get_settings()
    # Localhost only: the server acts with your AWS and GitHub accounts.
    uvicorn.run(
        "app.api:app",
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
        reload_dirs=["app"] if settings.reload else None,
    )


if __name__ == "__main__":
    main()
