"""``infra-agent`` command line.

Commands
--------
validate-config     Validate operator config + environment.
discover            List AWS services / READ operations from botocore models (offline).
check-credentials   Validate the read identity for an account (live, read-only).
read                Run one generic READ-only AWS operation (fixture or live).
investigate         ECS health playbook + optional GitHub correlation (fixture or live).

Live AWS access requires INFRA_AGENT_LIVE_READS=true AND a passing credential check.
There is no command that writes to AWS or GitHub.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from infra_agent.config import AccountConfig, AgentConfig, AppSettings, load_agent_config

FIXTURES = Path(__file__).parent / "fixtures"


class CliError(Exception):
    pass


def _settings_and_config(args: argparse.Namespace) -> tuple[AppSettings, AgentConfig]:
    settings = AppSettings()
    path = Path(args.config) if args.config else settings.config_path
    return settings, load_agent_config(path)


def _live_session(settings: AppSettings, account: AccountConfig) -> tuple[Any, Any]:
    """Return (guarded boto3 session, passing IdentityCheck) or raise."""
    if not settings.live_reads:
        raise CliError("live reads are disabled; set INFRA_AGENT_LIVE_READS=true to enable")
    import boto3

    from infra_agent.security import read_guard
    from infra_agent.security.credentials import validate_identity

    session = read_guard.install(boto3.Session(profile_name=account.profile))
    check = validate_identity(session, account)
    if not check.ok:
        raise CliError("credential validation failed: " + "; ".join(check.reasons))
    return session, check


def cmd_validate_config(args: argparse.Namespace) -> int:
    settings, cfg = _settings_and_config(args)
    print(f"config OK: {len(cfg.accounts)} account(s), {len(cfg.repositories)} repo(s)")
    print(f"live reads: {'enabled' if settings.live_reads else 'disabled'}; writes: disabled")
    return 0


def cmd_discover(args: argparse.Namespace) -> int:
    from infra_agent.aws import catalog
    from infra_agent.core.operations import AccessClass

    if not args.service:
        services = catalog.list_services()
        print(f"{len(services)} services")
        print("\n".join(services))
        return 0
    access = None if args.all else AccessClass.READ
    for spec in catalog.list_operations(args.service, access):
        print(f"{spec.access:15} {spec.ref.operation}")
    return 0


def cmd_check_credentials(args: argparse.Namespace) -> int:
    settings, cfg = _settings_and_config(args)
    account = cfg.account(args.account)
    _, check = _live_session(settings, account)
    print(f"identity: {check.display_arn}")
    print(f"mutation-denial proof: {check.simulation}")
    for reason in check.reasons:
        print(f"note: {reason}")
    return 0


def cmd_read(args: argparse.Namespace) -> int:
    from infra_agent.aws.fixtures import FixtureSession
    from infra_agent.aws.reader import AwsReader
    from infra_agent.core.operations import OperationRef, OperationRequest, ResourceScope

    settings, cfg = _settings_and_config(args)
    account = cfg.account(args.account)
    if args.fixture:
        session: Any = FixtureSession.from_file(Path(args.fixture))
    else:
        session, _ = _live_session(settings, account)
    reader = AwsReader(session, account)
    result = reader.call(
        OperationRequest(
            ref=OperationRef(provider="aws", service=args.service, operation=args.operation),
            scope=ResourceScope(account_alias=account.alias, region=args.region),
            params=json.loads(args.params),
            max_pages=args.max_pages,
        )
    )
    print(json.dumps(result.model_dump(mode="json"), indent=2, default=str))
    return 0


def cmd_investigate(args: argparse.Namespace) -> int:
    from infra_agent.aws.fixtures import FixtureSession
    from infra_agent.graph.workflow import (
        InvestigationDeps,
        InvestigationRequest,
        run_investigation,
    )
    from infra_agent.integrations.github_source import FixtureGitHubSource

    settings, cfg = _settings_and_config(args)
    account = cfg.account(args.account)

    identity = None
    if args.mock:
        session: Any = FixtureSession.from_file(Path(args.mock))
        mode = "mock"
    else:
        session, identity = _live_session(settings, account)
        mode = "live"

    github = (
        FixtureGitHubSource.from_file(Path(args.github_fixture)) if args.github_fixture else None
    )

    model = None
    if not args.no_llm:
        from infra_agent.integrations.bedrock import build_chat_model

        model = build_chat_model(cfg.model)

    deep_dive = None
    if args.agent:
        if mode != "live" or model is None:
            raise CliError("--agent needs live mode and a Bedrock model (omit --no-llm)")
        deep_dive = _make_deep_dive(cfg, account, session, identity, model, args.region)

    request: InvestigationRequest = {
        "account_alias": account.alias,
        "region": args.region,
        "cluster": args.cluster,
        "services": args.service or [],
        "repository": args.repo,
        "question": args.question,
    }
    deps = InvestigationDeps(
        config=cfg,
        session=session,
        mode=mode,  # type: ignore[arg-type]
        identity=identity,
        github=github,
        model=model,
        deep_dive=deep_dive,
    )
    state = asyncio.run(run_investigation(deps, request))
    if "report" not in state:
        print(f"task {state['task'].id}: {state['task'].status}", file=sys.stderr)
        for err in state.get("errors", []):
            print(f"  - {err}", file=sys.stderr)
        return 2
    report = state["report"]
    text = report.model_dump_json(indent=2) if args.json else report.to_markdown()
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"report written to {args.out}")
    else:
        print(text)
    return 0


def _make_deep_dive(
    cfg: AgentConfig, account: AccountConfig, session: Any, identity: Any, model: Any, region: str
) -> Any:
    import boto3  # noqa: F401  (ensures boto3 available in live mode)

    from infra_agent.agents.aws_tools import make_aws_tools
    from infra_agent.aws.reader import AwsReader
    from infra_agent.integrations.aws_mcp import open_aws_mcp_tools
    from infra_agent.integrations.bedrock import deep_dive

    local_tools = make_aws_tools(AwsReader(session, account), account.alias)
    creds = session.get_credentials().get_frozen_credentials()

    async def run(question: str) -> str:
        try:
            async with open_aws_mcp_tools(
                cfg.aws_mcp, identity, creds, region, correlation_id="deep-dive"
            ) as mcp:
                tools = [*mcp.tools, *local_tools]
                return await deep_dive(model, tools, question, cfg.limits.max_agent_steps)
        except PermissionError:
            raise
        except Exception as exc:
            notes = await deep_dive(model, local_tools, question, cfg.limits.max_agent_steps)
            return f"(AWS MCP unavailable: {type(exc).__name__}; used local read tools)\n\n{notes}"

    return run


def _platform(args: argparse.Namespace) -> Any:
    from infra_agent.runtime import Platform

    settings, cfg = _settings_and_config(args)
    return Platform.from_settings(cfg, settings)


def cmd_migrate(args: argparse.Namespace) -> int:
    from infra_agent.persistence.store import PostgresStore

    settings = AppSettings()
    if not settings.database_url:
        raise CliError("INFRA_AGENT_DATABASE_URL is not set")
    applied = PostgresStore(settings.database_url).migrate()
    print(f"applied: {applied or 'nothing (up to date)'}")
    return 0


def cmd_worker(args: argparse.Namespace) -> int:
    import logging

    from infra_agent.runtime import run_worker

    logging.basicConfig(level=AppSettings().log_level)
    platform = _platform(args)
    processed = _run_async(run_worker(platform, once=args.once))
    print(f"processed {processed} job(s)")
    return 0


def cmd_events(args: argparse.Namespace) -> int:
    import boto3

    from infra_agent.events.sqs import poll_once

    settings = AppSettings()
    if not settings.events_queue_url or not settings.events_profile:
        raise CliError("set INFRA_AGENT_EVENTS_QUEUE_URL and INFRA_AGENT_EVENTS_PROFILE")
    platform = _platform(args)
    sqs = boto3.Session(profile_name=settings.events_profile).client("sqs")
    while True:
        stats = poll_once(platform, sqs, settings.events_queue_url)
        print(stats, flush=True)
        if args.once:
            return 0


def cmd_submit(args: argparse.Namespace) -> int:
    from infra_agent.models.domain import Task

    platform = _platform(args)
    task = platform.submit(
        Task(
            requester=args.requester,
            origin="cli",
            intent=args.intent,
            account_alias=args.account,
            region=args.region,
            repositories=tuple(args.repo or ()),
        )
    )
    print(task.id)
    return 0


def cmd_hash_token(args: argparse.Namespace) -> int:
    import hashlib
    import secrets

    token = secrets.token_urlsafe(32)
    print(f"token (give to the user once): {token}")
    print(f"token_sha256 (put in config):  {hashlib.sha256(token.encode()).hexdigest()}")
    return 0


def _run_async(coro: Any) -> Any:
    if sys.platform == "win32":  # psycopg async needs a selector loop on Windows
        import selectors

        return asyncio.run(
            coro, loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())
        )
    return asyncio.run(coro)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="infra-agent", description=__doc__.split("\n")[0])
    p.add_argument("--config", help="Path to operator TOML config")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("validate-config").set_defaults(func=cmd_validate_config)

    d = sub.add_parser("discover")
    d.add_argument("--service")
    d.add_argument("--all", action="store_true", help="Include non-READ operations")
    d.set_defaults(func=cmd_discover)

    c = sub.add_parser("check-credentials")
    c.add_argument("--account", required=True)
    c.set_defaults(func=cmd_check_credentials)

    r = sub.add_parser("read")
    r.add_argument("--account", required=True)
    r.add_argument("--region", required=True)
    r.add_argument("--service", required=True)
    r.add_argument("--operation", required=True)
    r.add_argument("--params", default="{}", help="JSON object of API parameters")
    r.add_argument("--max-pages", type=int, default=1)
    r.add_argument("--fixture", help="Recorded-response JSON instead of live AWS")
    r.set_defaults(func=cmd_read)

    i = sub.add_parser("investigate")
    i.add_argument("--account", required=True)
    i.add_argument("--region", required=True)
    i.add_argument("--cluster", required=True)
    i.add_argument("--service", action="append", help="Limit to service (repeatable)")
    i.add_argument("--repo", help="owner/name for GitHub Actions correlation")
    i.add_argument("--question", help="Free-text question for the agent deep-dive")
    i.add_argument(
        "--mock",
        nargs="?",
        const=str(FIXTURES / "ecs_unhealthy.json"),
        help="Use recorded AWS responses (default sample if no path)",
    )
    i.add_argument(
        "--github-fixture",
        nargs="?",
        const=str(FIXTURES / "github_runs.json"),
        help="Use recorded GitHub runs (default sample if no path)",
    )
    i.add_argument("--no-llm", action="store_true", help="Deterministic summary, no Bedrock")
    i.add_argument("--agent", action="store_true", help="LLM deep-dive with read-only tools")
    i.add_argument("--json", action="store_true")
    i.add_argument("--out", help="Write report to file")
    i.set_defaults(func=cmd_investigate)

    sub.add_parser("migrate", help="Apply database migrations").set_defaults(func=cmd_migrate)

    w = sub.add_parser("worker", help="Run the task worker (plans, approvals, execution)")
    w.add_argument("--once", action="store_true", help="Process one job and exit")
    w.set_defaults(func=cmd_worker)

    e = sub.add_parser("events", help="Poll EventBridge events from SQS")
    e.add_argument("--once", action="store_true")
    e.set_defaults(func=cmd_events)

    s = sub.add_parser("submit", help="Submit a task to the queue")
    s.add_argument("--intent", required=True)
    s.add_argument("--account", required=True)
    s.add_argument("--region", required=True)
    s.add_argument("--repo", action="append")
    s.add_argument("--requester", default="cli-operator")
    s.set_defaults(func=cmd_submit)

    sub.add_parser("hash-token", help="Generate an API token and its sha256").set_defaults(
        func=cmd_hash_token
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (CliError, PermissionError, LookupError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
