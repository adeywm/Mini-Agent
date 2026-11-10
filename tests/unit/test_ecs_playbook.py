from __future__ import annotations

from infra_agent.aws.classifier import classify
from infra_agent.aws.fixtures import FixtureSession
from infra_agent.config import AgentConfig
from infra_agent.core.operations import AccessClass
from infra_agent.playbooks.ecs_health.collector import EcsCollector
from infra_agent.playbooks.ecs_health.models import FindingCategory as C
from infra_agent.playbooks.ecs_health.rules import analyze


def _collect(cfg: AgentConfig, session: FixtureSession) -> tuple[EcsCollector, object]:
    collector = EcsCollector(session, account_alias="dev", region="ap-south-1", limits=cfg.limits)
    return collector, collector.collect("dev-cluster")


def test_diagnosis_matches_fixture(cfg: AgentConfig, fixture_session: FixtureSession) -> None:
    collector, snapshot = _collect(cfg, fixture_session)
    findings, health = analyze(snapshot)  # type: ignore[arg-type]
    verdicts = {h.service: h.verdict for h in health}
    assert verdicts == {"api": "UNHEALTHY", "worker": "UNHEALTHY", "web": "HEALTHY"}

    cats = {(f.service, f.category) for f in findings}
    assert ("api", C.IMAGE_PULL_FAILURE) in cats
    assert ("api", C.DEPLOYMENT_FAILED) in cats
    assert ("api", C.TARGETS_UNHEALTHY) in cats
    assert ("worker", C.OUT_OF_MEMORY) in cats
    assert ("worker", C.DEPLOYMENT_STALLED) in cats
    assert not [f for f in findings if f.service == "web"]

    evidence_ids = {e.id for e in collector.evidence}
    for f in findings:
        assert set(f.evidence_ids) <= evidence_ids, f


def test_only_read_operations_called(cfg: AgentConfig, fixture_session: FixtureSession) -> None:
    _collect(cfg, fixture_session)
    calls = fixture_session.all_calls()
    assert calls
    assert all(classify(svc, op) is AccessClass.READ for svc, op, _ in calls)


def test_task_definition_secrets_never_collected(
    cfg: AgentConfig, fixture_session: FixtureSession
) -> None:
    collector, _ = _collect(cfg, fixture_session)
    dumped = str([e.model_dump() for e in collector.evidence])
    assert "do-not-leak-me" not in dumped
    assert "hunter2secret" not in dumped
    assert "DB_PASSWORD" not in dumped
