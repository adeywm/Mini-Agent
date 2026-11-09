"""Operator-owned configuration.

Two layers:

* ``AppSettings`` - process environment (``INFRA_AGENT_*``), loaded with pydantic-settings.
* ``AgentConfig`` - a TOML file the operator owns that lists accounts, regions, clusters,
  repositories, budgets, the Bedrock model and the AWS MCP endpoint.

Nothing here is inferred: an account, region or cluster the operator did not list is out of
scope and every component must refuse it.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Managed AWS MCP endpoint regions documented by AWS (Agent Toolkit user guide, Nov 2026).
# The endpoint region is NOT the target resource region.
SUPPORTED_MCP_ENDPOINT_REGIONS = frozenset(
    {
        "us-east-1",
        "us-west-2",
        "ap-southeast-1",
        "ap-southeast-2",
        "ap-northeast-1",
        "eu-central-1",
        "eu-west-1",
        "eu-west-2",
    }
)

_REGION_RE = re.compile(r"^[a-z]{2}(-gov)?-[a-z]+-\d$")
_ALIAS_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}$")
_CLUSTER_RE = re.compile(r"^[A-Za-z0-9_-]{1,255}$")

Region = Annotated[str, Field(pattern=_REGION_RE.pattern)]


class ConfigError(ValueError):
    """Raised when operator configuration is missing or invalid."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelConfig(_Strict):
    provider: Literal["bedrock"] = "bedrock"
    model_id: str = Field(min_length=1)
    region: Region
    temperature: float = Field(default=0.0, ge=0.0, le=1.0)
    max_tokens: int = Field(default=2000, ge=256, le=16000)


class AwsMcpConfig(_Strict):
    endpoint: str
    endpoint_region: Region
    sigv4_service: str = "aws-mcp"
    timeout_seconds: float = Field(default=60.0, gt=0, le=600)

    @model_validator(mode="after")
    def _check_endpoint(self) -> AwsMcpConfig:
        if self.endpoint_region not in SUPPORTED_MCP_ENDPOINT_REGIONS:
            raise ValueError(
                f"endpoint_region {self.endpoint_region!r} is not a documented managed "
                "AWS MCP endpoint region"
            )
        url = urlparse(self.endpoint)
        expected_host = f"aws-mcp.{self.endpoint_region}.api.aws"
        if url.scheme != "https" or url.hostname != expected_host or url.path != "/mcp":
            raise ValueError(f"endpoint must be https://{expected_host}/mcp")
        return self


class AccountConfig(_Strict):
    alias: str
    account_id: str = Field(pattern=r"^\d{12}$")
    environment: Literal["development", "staging", "production"]
    profile: str = Field(min_length=1, description="Read-only AWS CLI profile for this account")
    allowed_regions: tuple[Region, ...] = Field(min_length=1)
    allowed_clusters: tuple[str, ...] = Field(min_length=1)
    require_mutation_denial_proof: bool = Field(
        default=True,
        description=(
            "Refuse live reads unless IAM policy simulation proves probe mutations are denied."
        ),
    )

    @field_validator("alias")
    @classmethod
    def _alias(cls, v: str) -> str:
        if not _ALIAS_RE.match(v):
            raise ValueError("alias must be lowercase letters, digits and dashes")
        return v

    @field_validator("allowed_clusters")
    @classmethod
    def _clusters(cls, v: tuple[str, ...]) -> tuple[str, ...]:
        for name in v:
            if not _CLUSTER_RE.match(name):
                raise ValueError(f"invalid cluster name {name!r}")
        return v


class RepositoryConfig(_Strict):
    full_name: str
    account_alias: str

    @field_validator("full_name")
    @classmethod
    def _repo(cls, v: str) -> str:
        if not _REPO_RE.match(v):
            raise ValueError("repository must be 'owner/name'")
        return v


class LimitsConfig(_Strict):
    max_services: int = Field(default=20, ge=1, le=100)
    max_stopped_tasks_per_service: int = Field(default=10, ge=1, le=100)
    max_service_events: int = Field(default=10, ge=1, le=100)
    max_agent_steps: int = Field(default=12, ge=1, le=50)
    github_window_hours: int = Field(default=24, ge=1, le=168)


class BudgetConfig(_Strict):
    max_estimated_new_monthly_usd: float = Field(default=0.0, ge=0.0)


class ExecutionConfig(_Strict):
    # Phase 0/1: the type itself forbids enabling writes.
    aws_writes_enabled: Literal[False] = False
    github_writes_enabled: Literal[False] = False


class AgentConfig(_Strict):
    model: ModelConfig
    aws_mcp: AwsMcpConfig
    accounts: tuple[AccountConfig, ...] = Field(min_length=1)
    repositories: tuple[RepositoryConfig, ...] = ()
    limits: LimitsConfig = LimitsConfig()
    budgets: BudgetConfig = BudgetConfig()
    execution: ExecutionConfig = ExecutionConfig()

    @model_validator(mode="after")
    def _cross_checks(self) -> AgentConfig:
        aliases = [a.alias for a in self.accounts]
        if len(aliases) != len(set(aliases)):
            raise ValueError("account aliases must be unique")
        ids = [a.account_id for a in self.accounts]
        if len(ids) != len(set(ids)):
            raise ValueError("account ids must be unique")
        for repo in self.repositories:
            if repo.account_alias not in aliases:
                raise ValueError(f"repository {repo.full_name} references unknown account alias")
        return self

    def account(self, alias: str) -> AccountConfig:
        for acct in self.accounts:
            if acct.alias == alias:
                return acct
        raise ConfigError(f"account alias {alias!r} is not configured")

    def repository(self, full_name: str) -> RepositoryConfig:
        for repo in self.repositories:
            if repo.full_name == full_name:
                return repo
        raise ConfigError(f"repository {full_name!r} is not in the allowlist")


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="INFRA_AGENT_", env_file=".env", extra="ignore", frozen=True
    )

    app_env: Literal["local", "test", "dev"] = "local"
    config_path: Path = Path("config/agent.toml")
    live_reads: bool = False
    # Accepted only so that an attempt to enable writes fails loudly instead of being ignored.
    enable_aws_writes: bool = False
    enable_sandbox_writes: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @model_validator(mode="after")
    def _no_writes(self) -> AppSettings:
        if self.enable_aws_writes or self.enable_sandbox_writes:
            raise ValueError("AWS writes are not implemented in Phase 1 and cannot be enabled")
        return self


def load_agent_config(path: Path) -> AgentConfig:
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc
    try:
        return AgentConfig.model_validate(data)
    except ValueError as exc:
        raise ConfigError(f"invalid config {path}:\n{exc}") from exc
