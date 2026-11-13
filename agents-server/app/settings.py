"""Server settings from environment variables (prefix MINI_AGENT_) and agents-server/.env."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

SERVER_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MINI_AGENT_", env_file=SERVER_ROOT / ".env", extra="ignore", frozen=True
    )

    model_id: str = "global.anthropic.claude-sonnet-4-5-20250929-v1:0"
    model_region: str = "ap-south-1"
    model_max_tokens: int = 4000
    # Seconds to wait for Bedrock to send the next bytes. Some models take minutes to start
    # answering a long prompt; botocore's 60 s default fails those runs with TimeoutError.
    model_read_timeout: int = 300
    bedrock_profile: str | None = None

    database_path: Path = Path("../database/mini-agent.db")

    aws_mcp_endpoint: str = "https://aws-mcp.us-east-1.api.aws/mcp"
    aws_mcp_region: str = "us-east-1"
    github_mcp_url: str = "https://api.githubcopilot.com/mcp/"

    host: str = "127.0.0.1"
    port: int = 8000
    max_agent_steps: int = 40
    reload: bool = True  # restart on code changes; MINI_AGENT_RELOAD=false to turn off

    @property
    def db_file(self) -> Path:
        path = self.database_path
        return path if path.is_absolute() else (SERVER_ROOT / path).resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()
