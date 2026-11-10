from __future__ import annotations

from typing import Any

import pytest

from infra_agent.config import AccountConfig, AgentConfig
from infra_agent.security.credentials import PROBE_MUTATIONS, validate_identity

ROLE_SESSION_ARN = "arn:aws:sts::111122223333:assumed-role/InfraAgentReadOnly/op"


class _Client:
    def __init__(self, **responses: Any) -> None:
        self._r = responses

    def __getattr__(self, name: str) -> Any:
        def call(**_: Any) -> Any:
            value = self._r[name]
            if isinstance(value, Exception):
                raise value
            return value

        return call


class _Session:
    """Stands in for boto3.Session; ``read_guard.install`` registers on ``_session``."""

    def __init__(self, sts: Any, iam: Any) -> None:
        self._clients = {"sts": sts, "iam": iam}
        self._session = self
        self.registered: list[str] = []

    def register(self, event: str, *_: Any, **__: Any) -> None:
        self.registered.append(event)

    def client(self, name: str, **_: Any) -> Any:
        return self._clients[name]


def _session(
    account: str = "111122223333",
    arn: str = ROLE_SESSION_ARN,
    decisions: dict[str, str] | None = None,
    simulate_error: Exception | None = None,
) -> _Session:
    sts = _Client(get_caller_identity={"Account": account, "Arn": arn})
    results = [
        {"EvalActionName": a, "EvalDecision": (decisions or {}).get(a, "implicitDeny")}
        for a in PROBE_MUTATIONS
    ]
    iam = _Client(
        get_role={"Role": {"Arn": "arn:aws:iam::111122223333:role/InfraAgentReadOnly"}},
        simulate_principal_policy=simulate_error or {"EvaluationResults": results},
    )
    return _Session(sts, iam)


@pytest.fixture
def account(cfg: AgentConfig) -> AccountConfig:
    return cfg.account("dev")


def test_read_only_identity_passes(account: AccountConfig) -> None:
    s = _session()
    check = validate_identity(s, account)
    assert check.ok, check.reasons
    assert check.simulation == "passed"
    assert "before-call" in s.registered
    assert check.display_arn == "arn:aws:sts::1111****3333:assumed-role/InfraAgentReadOnly/op"


def test_wrong_account_refused(account: AccountConfig) -> None:
    check = validate_identity(_session(account="999988887777"), account)
    assert not check.ok
    assert "different account" in check.reasons[0]


def test_root_refused(account: AccountConfig) -> None:
    check = validate_identity(_session(arn="arn:aws:iam::111122223333:root"), account)
    assert not check.ok


def test_write_capable_identity_refused(account: AccountConfig) -> None:
    check = validate_identity(
        _session(decisions={"ecs:UpdateService": "allowed", "iam:PassRole": "allowed"}), account
    )
    assert not check.ok
    assert check.simulation == "failed"
    assert check.allowed_mutations == ["ecs:UpdateService", "iam:PassRole"]


def test_simulation_unavailable_fails_closed(account: AccountConfig) -> None:
    check = validate_identity(_session(simulate_error=PermissionError("AccessDenied")), account)
    assert not check.ok
    assert check.simulation == "unavailable"


def test_sts_failure_fails_closed(account: AccountConfig) -> None:
    s = _Session(_Client(get_caller_identity=RuntimeError("expired")), _Client())
    assert not validate_identity(s, account).ok


def test_proof_can_be_disabled_explicitly(account: AccountConfig) -> None:
    relaxed = account.model_copy(update={"require_mutation_denial_proof": False})
    check = validate_identity(_session(simulate_error=RuntimeError("x")), relaxed)
    assert check.ok
    assert check.simulation == "skipped"
    assert any("WARNING" in r for r in check.reasons)
