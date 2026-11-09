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
    allowed_clusters: tuple[str, ...] = Field(
        default=(), description="ECS playbook scope; empty means the playbook is not allowed"
    )
    permission_level: Literal[1, 2, 3] = Field(
        default=1, description="1 READ, 2 CREATE/UPDATE, 3 DELETE (see core.permissions)"
    )
    writes_enabled: bool = Field(
        default=False, description="Allow the execution broker to write to this account"
    )
    execution_profile: str | None = Field(
        default=None,
        description="Separate write-capable profile used ONLY by the execution broker",
    )
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

    @model_validator(mode="after")
    def _write_rules(self) -> AccountConfig:
        if self.writes_enabled:
            if self.environment == "production":
                raise ValueError("writes to production accounts are not enabled in this release")
            if not self.execution_profile:
                raise ValueError("writes_enabled requires a separate execution_profile")
            if self.execution_profile == self.profile:
                raise ValueError("execution_profile must differ from the read-only profile")
            if self.permission_level < 2:
                raise ValueError("writes_enabled requires permission_level >= 2")
        return self

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


class GitHubConfig(_Strict):
    """GitHub App used for writes (installation tokens scoped per repository).

    The private key is read from ``private_key_path`` at runtime; it must live outside
    the repository. Reads may use INFRA_AGENT_GITHUB_READ_TOKEN (fine-grained, read-only).
    """

    api_url: str = "https://api.github.com"
    app_id: int | None = None
    installation_id: int | None = None
    private_key_path: Path | None = None

    @field_validator("api_url")
    @classmethod
    def _https(cls, v: str) -> str:
        if urlparse(v).scheme != "https":
            raise ValueError("GitHub api_url must be https")
        return v.rstrip("/")


class ApiUser(_Strict):
    name: str = Field(min_length=1, max_length=64)
    roles: tuple[Literal["operator", "approver", "auditor", "admin"], ...] = Field(min_length=1)
    token_sha256: str = Field(pattern=r"^[0-9a-f]{64}$", description="sha256 of the bearer token")


class ApiConfig(_Strict):
    users: tuple[ApiUser, ...] = ()

    @model_validator(mode="after")
    def _unique(self) -> ApiConfig:
        names = [u.name for u in self.users]
        if len(names) != len(set(names)):
            raise ValueError("api user names must be unique")
        return self


class TriggerConfig(_Strict):
    """Maps a verified event to a task. Event text is passed as untrusted context only."""

    id: str = Field(min_length=1)
    source: Literal["github", "aws"]
    event: str = Field(description="GitHub event name or EventBridge detail-type (fnmatch)")
    match: dict[str, str] = Field(default_factory=dict, description="dotted path -> fnmatch")
    account_alias: str
    region: str | None = None
    repository: str | None = None
    intent: str = Field(min_length=1, max_length=2000)


class LimitsConfig(_Strict):
    max_services: int = Field(default=20, ge=1, le=100)
    max_stopped_tasks_per_service: int = Field(default=10, ge=1, le=100)
    max_service_events: int = Field(default=10, ge=1, le=100)
    max_agent_steps: int = Field(default=12, ge=1, le=50)
    planner_recursion_limit: int = Field(
        default=150, ge=20, le=1000, description="LangGraph steps for the planner deep agent"
    )
    github_window_hours: int = Field(default=24, ge=1, le=168)


class BudgetConfig(_Strict):
    max_estimated_new_monthly_usd: float = Field(default=0.0, ge=0.0)


class ExecutionConfig(_Strict):
    """Global write switches. A write needs ALL of: this flag, the process env flag
    INFRA_AGENT_ENABLE_AWS_WRITES, the account's writes_enabled, policy and (if required)
    a consumed human approval."""

    aws_writes_enabled: bool = False
    github_writes_enabled: bool = False
    approval_ttl_minutes: int = Field(default=60, ge=1, le=7 * 24 * 60)
    max_operations_per_plan: int = Field(default=20, ge=1, le=200)


class PolicyRule(_Strict):
    id: str = Field(min_length=1)
    effect: Literal["ALLOW_AUTO", "REQUIRE_APPROVAL", "DENY"]
    provider: Literal["aws", "github"] | None = None
    account_alias: str | None = None
    environment: Literal["development", "staging", "production"] | None = None
    service: str | None = None
    operation: str | None = Field(default=None, description="fnmatch pattern, e.g. Describe*")
    access: Literal["READ", "WRITE", "DELETE", "SENSITIVE_READ"] | None = None
    repository: str | None = None


class PolicyConfig(_Strict):
    rules: tuple[PolicyRule, ...] = ()


class AgentConfig(_Strict):
    model: ModelConfig
    aws_mcp: AwsMcpConfig
    accounts: tuple[AccountConfig, ...] = Field(min_length=1)
    repositories: tuple[RepositoryConfig, ...] = ()
    limits: LimitsConfig = LimitsConfig()
    budgets: BudgetConfig = BudgetConfig()
    execution: ExecutionConfig = ExecutionConfig()
    policy: PolicyConfig = PolicyConfig()
    github: GitHubConfig = GitHubConfig()
    api: ApiConfig = ApiConfig()
    triggers: tuple[TriggerConfig, ...] = ()

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
        for trig in self.triggers:
            if trig.account_alias not in aliases:
                raise ValueError(f"trigger {trig.id} references unknown account alias")
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
    # Process-level kill switch for writes; also requires config + account flags.
    enable_aws_writes: bool = False
    enable_github_writes: bool = False
    database_url: str | None = None
    github_read_token: str | None = Field(default=None, repr=False)
    github_webhook_secret: str | None = Field(default=None, repr=False)
    approval_queue_url: str | None = None
    events_queue_url: str | None = Field(default=None, description="SQS URL fed by EventBridge")
    events_profile: str | None = Field(default=None, description="Profile allowed to read SQS")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


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
