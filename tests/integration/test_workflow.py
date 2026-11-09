from __future__ import annotations

from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from infra_agent.aws.classifier import classify
from infra_agent.aws.fixtures import FixtureSession
from infra_agent.config import AgentConfig
from infra_agent.core.operations import AccessClass
from infra_agent.graph.workflow import InvestigationDeps, InvestigationRequest, run_investigation
from infra_agent.integrations.github_source import FixtureGitHubSource
from infra_agent.models.domain import TaskStatus
from infra_agent.security.credentials import IdentityCheck


def _request(**over: Any) -> InvestigationRequest:
    base: InvestigationRequest = {
        "account_alias": "dev",
        "region": "ap-south-1",
        "cluster": "dev-cluster",
        "services": [],
        "repository": "example-org/backend",
        "question": None,
    }
    return {**base, **over}  # type: ignore[typeddict-item]


async def test_mock_investigation_end_to_end(
    cfg: AgentConfig, fixture_session: FixtureSession, github_source: FixtureGitHubSource
) -> None:
    deps = InvestigationDeps(config=cfg, session=fixture_session, mode="mock", github=github_source)
    state = await run_investigation(deps, _request())

    assert state["task"].status is TaskStatus.COMPLETED
    report = state["report"]
    assert report.aws_write_calls == 0
    assert report.narrative_source == "deterministic"
    assert {h.service: h.verdict for h in report.services}["api"] == "UNHEALTHY"
    assert any(c.relation == "image_tag_matches_commit" for c in report.correlations)
    evidence = {e.id for e in report.evidence}
    for f in report.findings:
        assert set(f.evidence_ids) <= evidence
    for c in report.correlations:
        assert set(c.evidence_ids) <= evidence
    assert all(classify(svc, op) is AccessClass.READ for svc, op, _ in fixture_session.all_calls())
    md = report.to_markdown()
    assert "AWS write calls: **0**" in md
    assert [a.action for a in state["audit"]] == ["triage", "discover", "report"]


@pytest.mark.parametrize(
    ("over", "reason"),
    [
        ({"account_alias": "prod"}, "not configured"),
        ({"region": "us-east-1"}, "region us-east-1 not allowed"),
        ({"cluster": "prod-cluster"}, "cluster prod-cluster not allowed"),
        ({"repository": "attacker/repo"}, "not in the allowlist"),
        ({"services": ["api; rm -rf /"]}, "invalid service names"),
    ],
)
async def test_out_of_scope_requests_denied_without_aws_calls(
    cfg: AgentConfig, fixture_session: FixtureSession, over: dict[str, Any], reason: str
) -> None:
    deps = InvestigationDeps(config=cfg, session=fixture_session, mode="mock")
    state = await run_investigation(deps, _request(**over))
    assert state["task"].status is TaskStatus.DENIED
    assert "report" not in state
    assert any(reason in e for e in state["errors"])
    assert fixture_session.all_calls() == []


async def test_live_mode_requires_validated_identity(
    cfg: AgentConfig, fixture_session: FixtureSession
) -> None:
    for identity in (None, IdentityCheck(account_alias="dev", status="failed")):
        deps = InvestigationDeps(
            config=cfg, session=fixture_session, mode="live", identity=identity
        )
        state = await run_investigation(deps, _request())
        assert state["task"].status is TaskStatus.DENIED
    assert fixture_session.all_calls() == []


async def test_bedrock_narrative_used_and_failure_falls_back(
    cfg: AgentConfig, fixture_session: FixtureSession
) -> None:
    model = FakeListChatModel(responses=["api cannot pull its image [F-api-03]."])
    deps = InvestigationDeps(config=cfg, session=fixture_session, mode="mock", model=model)
    report = (await run_investigation(deps, _request(repository=None)))["report"]
    assert report.narrative_source == "bedrock"
    assert "F-api-03" in report.narrative

    class Broken(FakeListChatModel):
        def invoke(self, *_: Any, **__: Any) -> Any:
            raise RuntimeError("throttled")

    deps = InvestigationDeps(
        config=cfg,
        session=FixtureSession.from_file(fixture_session_path()),
        mode="mock",
        model=Broken(responses=[""]),
    )
    report = (await run_investigation(deps, _request(repository=None)))["report"]
    assert report.narrative_source == "deterministic"
    assert any("Bedrock narrative failed" in item for item in report.limitations)


async def test_discovery_failure_marks_task_failed(cfg: AgentConfig) -> None:
    deps = InvestigationDeps(config=cfg, session=FixtureSession({}), mode="mock")
    state = await run_investigation(deps, _request(repository=None))
    assert state["task"].status is TaskStatus.FAILED
    assert "report" not in state


def fixture_session_path() -> Any:
    from tests.conftest import FIXTURES

    return FIXTURES / "ecs_unhealthy.json"
