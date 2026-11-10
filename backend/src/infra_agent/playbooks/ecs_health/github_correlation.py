"""Correlate ECS deployments with GitHub Actions runs (S2)."""

from __future__ import annotations

from datetime import timedelta

from infra_agent.models.domain import Evidence
from infra_agent.models.report import GitHubCorrelation, WorkflowRunRef
from infra_agent.playbooks.ecs_health.models import EcsSnapshot


def _image_tags(images: tuple[str, ...]) -> list[str]:
    tags = []
    for image in images:
        ref = image.split("@", 1)[0]
        if ":" in ref.rsplit("/", 1)[-1]:
            tags.append(ref.rsplit(":", 1)[-1].lower())
    return tags


def correlate(
    snapshot: EcsSnapshot,
    repository: str,
    runs: list[WorkflowRunRef],
    window_hours: int,
) -> tuple[list[GitHubCorrelation], list[Evidence]]:
    correlations: list[GitHubCorrelation] = []
    evidence: dict[str, Evidence] = {}

    def ev_for(run: WorkflowRunRef) -> str:
        ev_id = f"ev-gh-run-{run.id}"
        evidence.setdefault(
            ev_id,
            Evidence(
                id=ev_id,
                source="github:actions:workflow_runs",
                collected_at=snapshot.collected_at,
                summary=f"{repository} {run.workflow_name} #{run.id} {run.status}/{run.conclusion} "
                f"sha {run.head_sha[:7]} on {run.head_branch}",
                data=run.model_dump(mode="json"),
            ),
        )
        return ev_id

    window = timedelta(hours=window_hours)
    for svc in snapshot.services:
        tags = _image_tags(svc.container_images)
        primary = next((d for d in svc.deployments if d.status == "PRIMARY"), None)
        for run in runs:
            sha = run.head_sha.lower()
            if any(len(t) >= 7 and sha.startswith(t) for t in tags):
                correlations.append(
                    GitHubCorrelation(
                        service=svc.name,
                        repository=repository,
                        run=run,
                        relation="image_tag_matches_commit",
                        evidence_ids=(f"ev-svc-{svc.name}", ev_for(run)),
                    )
                )
            elif (
                primary is not None
                and primary.created_at is not None
                and run.conclusion in ("failure", "timed_out", "cancelled")
                and primary.created_at - window <= run.created_at <= primary.created_at + window
            ):
                correlations.append(
                    GitHubCorrelation(
                        service=svc.name,
                        repository=repository,
                        run=run,
                        relation="failed_run_near_deployment",
                        evidence_ids=(f"ev-svc-{svc.name}", ev_for(run)),
                    )
                )
    return correlations, list(evidence.values())
