"""Durable task processing for the operations workflow."""

from __future__ import annotations

import asyncio
import logging
import socket
from typing import TYPE_CHECKING, Any

from infra_agent.config import ConfigError
from infra_agent.graph.operations import (
    build_operations_graph,
    resume_task,
    start_task,
    thread_config,
)
from infra_agent.models.domain import TaskStatus
from infra_agent.persistence.queue import Job

if TYPE_CHECKING:
    from infra_agent.runtime import Platform

log = logging.getLogger("infra_agent.runtime")


async def run_job(graph: Any, platform: Platform, job: Job) -> dict[str, Any]:
    stored = platform.store.get_task(job.task_id)
    if stored is not None and stored.status is TaskStatus.CANCELLED:
        return {"outcome": "cancelled - skipped"}
    config = thread_config(job.task_id)
    snapshot = await graph.aget_state(config)
    if job.kind == "resume":
        return await resume_task(graph, job.task_id, str(job.payload.get("approval_id", "")))
    if snapshot.next:  # crashed mid-run: continue from the checkpoint, never restart
        if any(t.interrupts for t in snapshot.tasks):
            return {"outcome": "awaiting approval"}
        result: dict[str, Any] = await graph.ainvoke(None, config)
        return result
    if snapshot.values:
        return {"outcome": "already finished"}
    task = platform.store.get_task(job.task_id)
    if task is None:
        raise LookupError(f"task {job.task_id} not found")
    return await start_task(graph, task)


async def run_worker(platform: Platform, *, once: bool = False, poll_seconds: float = 2.0) -> int:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    if not platform.settings.database_url:
        raise ConfigError("worker requires INFRA_AGENT_DATABASE_URL")
    owner = f"{socket.gethostname()}-{id(platform)}"
    processed = 0
    async with AsyncPostgresSaver.from_conn_string(platform.settings.database_url) as saver:
        await saver.setup()
        graph = build_operations_graph(platform.ops_deps(), saver)
        while True:
            job = await asyncio.to_thread(platform.queue.claim, owner)
            if job is None:
                if once:
                    return processed
                await asyncio.sleep(poll_seconds)
                continue
            try:
                result = await run_job(graph, platform, job)
                log.info("job %s task %s: %s", job.id, job.task_id, result.get("outcome"))
                await asyncio.to_thread(platform.queue.complete, job)
            except Exception as exc:
                log.exception("job %s failed", job.id)
                await asyncio.to_thread(platform.queue.fail, job, f"{type(exc).__name__}: {exc}")
            processed += 1
