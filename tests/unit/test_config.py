from __future__ import annotations

import copy
from pathlib import Path

import pytest
from pydantic import ValidationError

from infra_agent.config import AgentConfig, AppSettings, ConfigError, load_agent_config
from tests.conftest import config_dict


def test_example_config_loads(cfg: AgentConfig) -> None:
    assert cfg.account("dev").allowed_regions == ("ap-south-1",)
    assert cfg.execution.aws_writes_enabled is False


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("execution", "aws_writes_enabled"), True),
        (("execution", "github_writes_enabled"), True),
        (("aws_mcp", "endpoint"), "http://aws-mcp.us-east-1.api.aws/mcp"),
        (("aws_mcp", "endpoint"), "https://evil.example.com/mcp"),
        (("aws_mcp", "endpoint_region"), "ap-south-1"),
        (("model", "provider"), "openai"),
    ],
)
def test_invalid_values_rejected(path: tuple[str, str], value: object) -> None:
    data = copy.deepcopy(config_dict())
    data[path[0]][path[1]] = value
    with pytest.raises(ValidationError):
        AgentConfig.model_validate(data)


def test_bad_account_fields_rejected() -> None:
    for field, value in [
        ("account_id", "12345"),
        ("alias", "Dev Account"),
        ("allowed_regions", ["mumbai"]),
        ("allowed_clusters", []),
    ]:
        data = copy.deepcopy(config_dict())
        data["accounts"][0][field] = value
        with pytest.raises(ValidationError):
            AgentConfig.model_validate(data)


def test_duplicate_alias_and_unknown_repo_alias() -> None:
    data = copy.deepcopy(config_dict())
    data["accounts"].append(dict(data["accounts"][0], account_id="444455556666"))
    with pytest.raises(ValidationError, match="unique"):
        AgentConfig.model_validate(data)
    data = copy.deepcopy(config_dict())
    data["repositories"][0]["account_alias"] = "prod"
    with pytest.raises(ValidationError, match="unknown account alias"):
        AgentConfig.model_validate(data)


def test_unknown_keys_rejected() -> None:
    data = copy.deepcopy(config_dict())
    data["accounts"][0]["role_arn"] = "arn:aws:iam::111122223333:role/Admin"
    with pytest.raises(ValidationError):
        AgentConfig.model_validate(data)


def test_lookup_of_unconfigured_scope(cfg: AgentConfig) -> None:
    with pytest.raises(ConfigError):
        cfg.account("prod")
    with pytest.raises(ConfigError):
        cfg.repository("someone/else")


def test_missing_and_malformed_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_agent_config(tmp_path / "nope.toml")
    bad = tmp_path / "bad.toml"
    bad.write_text("[model\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_agent_config(bad)


@pytest.mark.parametrize("var", ["INFRA_AGENT_ENABLE_AWS_WRITES", "INFRA_AGENT_ENABLE_SANDBOX_WRITES"])
def test_env_cannot_enable_writes(monkeypatch: pytest.MonkeyPatch, var: str) -> None:
    monkeypatch.setenv(var, "true")
    with pytest.raises(ValidationError, match="cannot be enabled"):
        AppSettings()


def test_live_reads_default_off() -> None:
    assert AppSettings().live_reads is False
