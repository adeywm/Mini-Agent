"""Verified event -> task mapping (GitHub webhooks, EventBridge via SQS).

Events never carry authority: they can only start a task whose intent comes from operator
configuration. Event content is attached as untrusted context for the planner, and the
normal policy/approval/broker path decides what (if anything) is executed.
"""

from __future__ import annotations

import fnmatch
import hashlib
import hmac
import json
from typing import Any

from infra_agent.config import AgentConfig, TriggerConfig
from infra_agent.execution.verifier import get_path
from infra_agent.models.domain import Task
from infra_agent.security.redaction import redact

MAX_CONTEXT_CHARS = 4000


class EventRejected(ValueError):
    pass


def verify_github_signature(secret: str, body: bytes, signature_header: str | None) -> None:
    if not signature_header or not signature_header.startswith("sha256="):
        raise EventRejected("missing signature")
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature_header):
        raise EventRejected("bad signature")


def _matches(trigger: TriggerConfig, payload: dict[str, Any]) -> bool:
    for path, pattern in trigger.match.items():
        try:
            value = get_path(payload, path)
        except KeyError:
            return False
        if not fnmatch.fnmatchcase(str(value), pattern):
            return False
    return True


def tasks_for_event(
    config: AgentConfig,
    source: str,
    event: str,
    payload: dict[str, Any],
) -> list[Task]:
    tasks: list[Task] = []
    for trig in config.triggers:
        if trig.source != source or not fnmatch.fnmatchcase(event, trig.event):
            continue
        if not _matches(trig, payload):
            continue
        account = config.account(trig.account_alias)
        repo = trig.repository
        if source == "github":
            repo = repo or str(get_path(payload, "repository.full_name"))
            config.repository(repo)  # must be allowlisted
        context = json.dumps(redact(payload), default=str)[:MAX_CONTEXT_CHARS]
        tasks.append(
            Task(
                requester=f"trigger:{trig.id}",
                origin="webhook" if source == "github" else "event",
                intent=(
                    f"{trig.intent}\n\n<untrusted_event_context>\n{context}\n"
                    "</untrusted_event_context>"
                ),
                account_alias=account.alias,
                region=trig.region or account.allowed_regions[0],
                repositories=(repo,) if repo else (),
            )
        )
    return tasks


def parse_eventbridge(config: AgentConfig, body: str) -> tuple[str, str, dict[str, Any]]:
    """Validate an EventBridge envelope (delivered via SQS). Returns (id, detail-type, event)."""
    event = json.loads(body)
    for key in ("id", "detail-type", "account", "region", "source"):
        if key not in event:
            raise EventRejected(f"not an EventBridge event (missing {key})")
    known = {a.account_id: a for a in config.accounts}
    account = known.get(str(event["account"]))
    if account is None:
        raise EventRejected("event from an unconfigured account")
    if event["region"] not in account.allowed_regions:
        raise EventRejected("event from a region outside account scope")
    return str(event["id"]), str(event["detail-type"]), event
