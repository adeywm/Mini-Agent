from __future__ import annotations

import pytest

from infra_agent.execution.broker import MockExecutionBroker
from infra_agent.models.domain import ChangePlan, ExecutionStatus
from infra_agent.security.redaction import REDACTED, mask_account_ids, redact, redact_text
from tests.unit.test_models import _op

# Built from parts so that secret scanners don't flag the test file itself.
AWS_KEY = "AKIA" + "IOSFODNN7EXAMPLE"
GH_TOKEN = "ghp_" + "a" * 36


@pytest.mark.parametrize(
    "text",
    [
        f"key {AWS_KEY} leaked",
        f"token {GH_TOKEN}",
        "password=hunter2secret",
        'DB_PASSWORD: "s3cr3tvalue"',
        "Authorization: Bearer abcdefghijklmnop",
        "postgres://admin:pa55word@db.internal:5432/app",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----",
    ],
)
def test_secrets_redacted(text: str) -> None:
    out = redact_text(text)
    assert REDACTED in out
    for secret in (AWS_KEY, GH_TOKEN, "hunter2secret", "s3cr3tvalue", "pa55word", "MIIabc"):
        assert secret not in out


def test_structured_redaction_drops_secret_keys() -> None:
    data = {
        "environment": [{"name": "DB_PASSWORD", "value": "x"}],
        "secrets": [{"valueFrom": "arn"}],
        "nested": [{"SecretString": "y", "ok": f"see {AWS_KEY}"}],
        "count": 3,
    }
    out = redact(data)
    assert out["environment"] == REDACTED
    assert out["secrets"] == REDACTED
    assert out["nested"][0]["SecretString"] == REDACTED
    assert AWS_KEY not in str(out)
    assert out["count"] == 3


def test_plain_text_untouched() -> None:
    msg = "(service api) has reached a steady state."
    assert redact_text(msg) == msg


def test_mask_account_ids() -> None:
    assert (
        mask_account_ids("arn:aws:sts::111122223333:assumed-role/R/s")
        == "arn:aws:sts::1111****3333:assumed-role/R/s"
    )


def test_mock_broker_refuses_and_records(monkeypatch: pytest.MonkeyPatch) -> None:
    import boto3

    def boom(*_: object, **__: object) -> None:
        raise AssertionError("broker must not create AWS clients")

    monkeypatch.setattr(boto3, "client", boom)
    monkeypatch.setattr(boto3, "Session", boom)
    broker = MockExecutionBroker()
    op = _op()
    plan = ChangePlan(id="p", version=1, task_id="t", operations=(op,))
    rec = broker.execute(plan, op, approval=None)
    assert broker.writes_enabled is False
    assert rec.status is ExecutionStatus.BLOCKED
    assert rec.plan_digest == plan.digest
    assert broker.records == [rec]
