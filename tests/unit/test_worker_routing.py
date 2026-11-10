"""Verify worker ownership and cancelled-task routing without a database."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from infra_agent.models.domain import TaskStatus
from infra_agent.persistence.queue import Job
from infra_agent.runtime import run_job as legacy_run_job
from infra_agent.workers.runner import run_job


def test_worker_import_remains_compatible() -> None:
    assert legacy_run_job is run_job


@pytest.mark.asyncio
async def test_cancelled_job_does_not_load_graph() -> None:
    graph = SimpleNamespace(aget_state=AsyncMock())
    store = SimpleNamespace(
        get_task=Mock(return_value=SimpleNamespace(status=TaskStatus.CANCELLED))
    )
    platform = SimpleNamespace(store=store)
    result = await run_job(graph, platform, Job(1, "task-1", "start", {}, 1))
    assert result == {"outcome": "cancelled - skipped"}
    graph.aget_state.assert_not_awaited()
