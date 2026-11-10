from __future__ import annotations

import copy
from pathlib import Path

import pytest
from pydantic import ValidationError

from infra_agent.config import (
    PROJECT_ROOT,
    AgentConfig,
    AppSettings,
    ConfigError,
    load_agent_config,
)
from tests.conftest import config_dict


def test_example_config_loads(cfg: AgentConfig) -> None:
    assert cfg.account("dev").allowed_regions == ("ap-south-1",)
    assert cfg.execution.aws_writes_enabled is False


@pytest.mark.parametrize(
    ("path", "value"),
    [
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
        ("permission_level", 4),
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


@pytest.mark.parametrize(
    "acct",
    [
        {"writes_enabled": True},  # no execution profile
        {"writes_enabled": True, "execution_profile": "infra-agent-readonly-dev"},  # same as read
        {"writes_enabled": True, "execution_profile": "w", "permission_level": 1},
        {
            "writes_enabled": True,
            "execution_profile": "w",
            "permission_level": 2,
            "environment": "production",
        },
    ],
)
def test_unsafe_write_configs_rejected(acct: dict[str, object]) -> None:
    data = copy.deepcopy(config_dict())
    data["accounts"][0].update(acct)
    with pytest.raises(ValidationError):
        AgentConfig.model_validate(data)


def test_writes_default_off(cfg: AgentConfig) -> None:
    assert cfg.execution.aws_writes_enabled is False
    assert cfg.account("dev").writes_enabled is False
    assert AppSettings().enable_aws_writes is False


def test_live_reads_default_off() -> None:
    assert AppSettings().live_reads is False


def test_settings_paths_do_not_depend_on_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("INFRA_AGENT_CONFIG_PATH", "config/agent.example.toml")
    settings = AppSettings()
    assert settings.config_path == PROJECT_ROOT / "config" / "agent.example.toml"
    assert Path(str(AppSettings.model_config["env_file"])) == PROJECT_ROOT / "backend" / ".env"
    assert load_agent_config(settings.config_path).accounts
