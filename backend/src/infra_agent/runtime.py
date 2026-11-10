"""Wires configuration into a running platform: store, queue, readers, brokers, planner,
operations graph and the background worker.

Process roles:
* API (``infra_agent.api.main``): accepts tasks/approvals/webhooks, enqueues work.
* Worker (``infra-agent worker``): claims queue jobs and runs/resumes the LangGraph
  workflow with a PostgreSQL checkpointer, so tasks survive restarts.
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass, field
from typing import Any

from infra_agent.aws.reader import AwsReader
from infra_agent.config import AccountConfig, AgentConfig, AppSettings, ConfigError
from infra_agent.execution.aws_broker import AwsExecutionBroker
from infra_agent.execution.verifier import verify_aws
from infra_agent.github.client import (
    GitHubAppTokenProvider,
    GitHubExecutionBroker,
    GitHubReader,
    verify_github,
)
from infra_agent.graph.operations import (
    OpsDeps,
    PlanProposal,
)
from infra_agent.models.domain import PlannedOperation, Task
from infra_agent.persistence.queue import InMemoryQueue, PostgresQueue, WorkQueue
from infra_agent.persistence.store import InMemoryStore, PostgresStore, Store
from infra_agent.workers.runner import run_job as run_job
from infra_agent.workers.runner import run_worker as run_worker

log = logging.getLogger("infra_agent.runtime")


@dataclass
class Platform:
    config: AgentConfig
    settings: AppSettings
    store: Store
    queue: WorkQueue
    readers: dict[str, AwsReader] = field(default_factory=dict)
    github_reader: GitHubReader | None = None
    planner_model: Any = None
    aws_session_factory: Any = None  # tests inject stubbed sessions; default: boto3 profile
    planner_override: Any = None  # tests inject a deterministic planner
    identities: dict[str, tuple[Any, Any]] = field(
        default_factory=dict
    )  # alias -> (session, check)

    @classmethod
    def from_settings(cls, config: AgentConfig, settings: AppSettings) -> Platform:
        if settings.database_url:
            store: Store = PostgresStore(settings.database_url)
            store.migrate()  # type: ignore[attr-defined]
            queue: WorkQueue = PostgresQueue(settings.database_url)
        elif settings.app_env == "test":
            store, queue = InMemoryStore(), InMemoryQueue()
        else:
            raise ConfigError("INFRA_AGENT_DATABASE_URL is required outside tests")
        return cls(
            config=config,
            settings=settings,
            store=store,
            queue=queue,
            github_reader=GitHubReader(config, settings.github_read_token),
        )

    # --- read paths -------------------------------------------------------------------
    def reader_for(self, account: AccountConfig) -> AwsReader | None:
        if account.alias in self.readers:
            return self.readers[account.alias]
        if not self.settings.live_reads:
            return None
        from infra_agent.security import read_guard
        from infra_agent.security.credentials import validate_identity

        session = read_guard.guarded_session(account.profile)
        check = validate_identity(session, account)
        if not check.ok:
            log.error("read identity for %s refused: %s", account.alias, "; ".join(check.reasons))
            return None
        self.identities[account.alias] = (session, check)
        self.readers[account.alias] = AwsReader(session, account)
        return self.readers[account.alias]

    # --- brokers ----------------------------------------------------------------------
    def github_tokens(self, repo: str, perms: dict[str, str]) -> str:
        gh = self.config.github
        if not (gh.app_id and gh.installation_id and gh.private_key_path):
            raise PermissionError("GitHub App not configured; GitHub writes unavailable")
        provider = GitHubAppTokenProvider(
            gh.app_id, gh.installation_id, gh.private_key_path, gh.api_url
        )
        return provider.token_for(repo, perms)

    def ops_deps(self) -> OpsDeps:
        aws_broker = (
            AwsExecutionBroker(self.config, self.settings, self.store, self.aws_session_factory)
            if self.aws_session_factory
            else AwsExecutionBroker(self.config, self.settings, self.store)
        )
        gh_broker = GitHubExecutionBroker(
            self.config, self.settings, self.store, self.github_tokens
        )
        readers = {a.alias: r for a in self.config.accounts if (r := self.reader_for(a))}

        def verify_aws_op(op: PlannedOperation) -> tuple[bool, str]:
            reader = readers.get(op.account_alias)
            return verify_aws(reader, op) if reader else (False, "no read path to verify")

        def verify_github_op(op: PlannedOperation) -> tuple[bool, str]:
            if self.github_reader is None:
                return False, "no GitHub read path"
            return verify_github(self.github_reader, op)

        return OpsDeps(
            config=self.config,
            store=self.store,
            planner=self.plan,
            executors={"aws": aws_broker.execute, "github": gh_broker.execute},
            verifiers={"aws": verify_aws_op, "github": verify_github_op},
            readers=readers,
        )

    async def plan(self, task: Task) -> PlanProposal:
        if self.planner_override is not None:
            result: PlanProposal = await self.planner_override(task)
            return result
        from infra_agent.agents.aws_tools import make_aws_tools
        from infra_agent.agents.planner import make_deep_agent_planner, make_github_tools
        from infra_agent.integrations.bedrock import build_chat_model

        model = self.planner_model or build_chat_model(self.config.model)
        reader = self.reader_for(self.config.account(task.account_alias))
        aws_tools = make_aws_tools(reader, task.account_alias) if reader else []
        gh_tools = make_github_tools(self.github_reader) if self.github_reader else []
        steps = self.config.limits.planner_recursion_limit
        identity = self.identities.get(task.account_alias)
        async with contextlib.AsyncExitStack() as stack:
            mcp = None
            if identity is not None:
                # Dynamic AWS discovery via the managed AWS MCP Server, under the validated
                # read-only identity. Only a failure to CONNECT falls back to local tools;
                # planner errors propagate.
                from infra_agent.integrations.aws_mcp import open_aws_mcp_tools

                session, check = identity
                try:
                    creds = session.get_credentials().get_frozen_credentials()
                    mcp = await stack.enter_async_context(
                        open_aws_mcp_tools(
                            self.config.aws_mcp, check, creds, task.region, correlation_id=task.id
                        )
                    )
                except PermissionError:
                    raise
                except Exception as exc:
                    log.warning(
                        "AWS MCP unavailable for %s (%s); using local read tools",
                        task.id,
                        type(exc).__name__,
                    )
            tools = [*mcp.tools, *aws_tools] if mcp else aws_tools
            planner = make_deep_agent_planner(
                model, tools, gh_tools, max_steps=steps, store=self.store
            )
            try:
                return await planner(task)
            finally:
                if mcp:
                    self.store.append_audit(mcp.guard.audit)

    # --- task intake --------------------------------------------------------------------
    def submit(self, task: Task) -> Task:
        self.store.save_task(task)
        self.queue.enqueue(task.id, "start", {}, dedupe_key=f"start:{task.id}")
        return task
