"""GitHub Actions read source (S2 correlation).

Phase 1 ships the interface and a fixture source. The live GitHub MCP (read-only) adapter is
not implemented yet; see README "Remaining limitations".
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Protocol

from infra_agent.models.report import WorkflowRunRef
from infra_agent.security.redaction import redact


class GitHubRunSource(Protocol):
    def list_workflow_runs(
        self, repository: str, since: datetime, until: datetime
    ) -> list[WorkflowRunRef]: ...


class FixtureGitHubSource:
    def __init__(self, data: dict[str, list[dict[str, object]]]) -> None:
        self._data = data

    @classmethod
    def from_file(cls, path: Path) -> FixtureGitHubSource:
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def list_workflow_runs(
        self, repository: str, since: datetime, until: datetime
    ) -> list[WorkflowRunRef]:
        runs = [WorkflowRunRef.model_validate(redact(r)) for r in self._data.get(repository, [])]
        return [r for r in runs if since <= r.created_at <= until]
