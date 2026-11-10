"""Diagnostic report produced by the read-only investigation."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from infra_agent.models.domain import Evidence
from infra_agent.playbooks.ecs_health.models import Finding, ServiceHealth


class WorkflowRunRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: int
    workflow_name: str
    head_sha: str
    head_branch: str | None = None
    event: str
    status: str
    conclusion: str | None = None
    created_at: datetime
    html_url: str


class GitHubCorrelation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    service: str
    repository: str
    run: WorkflowRunRef
    relation: Literal["image_tag_matches_commit", "failed_run_near_deployment"]
    evidence_ids: tuple[str, ...]


class DiagnosticReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    mode: Literal["mock", "live"]
    account_alias: str
    region: str
    cluster: str
    generated_at: datetime
    caller_identity: str | None = Field(default=None, description="Redacted principal ARN")
    services: tuple[ServiceHealth, ...]
    findings: tuple[Finding, ...]
    correlations: tuple[GitHubCorrelation, ...] = ()
    evidence: tuple[Evidence, ...]
    narrative: str
    narrative_source: Literal["bedrock", "deterministic"]
    agent_notes: str | None = Field(
        default=None, description="Optional LLM deep-dive output; unverified, LLM-generated"
    )
    limitations: tuple[str, ...] = ()
    aws_write_calls: Literal[0] = 0

    def to_markdown(self) -> str:
        lines = [
            f"# ECS diagnostic report - {self.cluster}",
            "",
            f"- Task: `{self.task_id}`  Mode: **{self.mode}**",
            f"- Account: `{self.account_alias}`  Region: `{self.region}`",
            f"- Generated: {self.generated_at.isoformat()}",
        ]
        if self.caller_identity:
            lines.append(f"- Read identity: `{self.caller_identity}`")
        lines += ["- AWS write calls: **0** (writes are disabled)", "", "## Summary", ""]
        lines += [self.narrative, "", "## Services", ""]
        lines += ["| Service | Verdict | Running/Desired | Findings |", "|---|---|---|---|"]
        for svc in self.services:
            lines.append(
                f"| {svc.service} | {svc.verdict} | {svc.running_count}/{svc.desired_count} "
                f"| {', '.join(svc.finding_ids) or '-'} |"
            )
        lines += ["", "## Findings", ""]
        if not self.findings:
            lines.append("No problems found in the collected evidence.")
        for f in self.findings:
            lines.append(f"### {f.id} [{f.severity}] {f.title}")
            lines.append(f"Service `{f.service}` - {f.category}")
            lines.append("")
            lines.append(f.detail)
            lines.append(f"Evidence: {', '.join(f'`{e}`' for e in f.evidence_ids)}")
            lines.append("")
        if self.correlations:
            lines += ["## GitHub Actions correlation", ""]
            for c in self.correlations:
                lines.append(
                    f"- `{c.service}` <- {c.repository} run {c.run.id} "
                    f"({c.run.workflow_name}, {c.run.conclusion}, sha {c.run.head_sha[:7]}): "
                    f"{c.relation} - {c.run.html_url}"
                )
            lines.append("")
        if self.agent_notes:
            lines += ["## Agent deep-dive (LLM-generated, unverified)", "", self.agent_notes, ""]
        lines += ["## Evidence", ""]
        for e in self.evidence:
            lines.append(f"- `{e.id}` {e.source} @ {e.collected_at.isoformat()}: {e.summary}")
        if self.limitations:
            lines += ["", "## Limitations", ""]
            lines += [f"- {item}" for item in self.limitations]
        return "\n".join(lines) + "\n"
