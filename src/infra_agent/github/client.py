"""Generic GitHub REST access: one code path for every endpoint.

* ``GitHubReader`` executes GET requests for allowlisted repositories only; responses are
  redacted and recorded as evidence. SENSITIVE_READ paths (secrets, keys, tokens) refused.
* ``GitHubAppTokenProvider`` mints short-lived installation tokens restricted to ONE
  repository and the minimum permissions for the write being made.
* ``GitHubExecutionBroker`` is the only GitHub write path, with the same gates as the AWS
  broker: plan membership, policy, write switches, approval, idempotency, audit.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx

from infra_agent.config import AgentConfig, AppSettings, ConfigError
from infra_agent.core.operations import AccessClass, OperationDenied
from infra_agent.github.classifier import classify_github, repo_of
from infra_agent.models.domain import (
    AuditEvent,
    ChangePlan,
    Decision,
    Evidence,
    ExecutionRecord,
    ExecutionStatus,
    PlannedOperation,
    sha256_digest,
    utcnow,
)
from infra_agent.persistence.store import Store
from infra_agent.policy.engine import PolicyEngine
from infra_agent.security.redaction import redact

_HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
_MAX_BYTES = 2_000_000


class GitHubReader:
    def __init__(
        self, config: AgentConfig, token: str | None, transport: httpx.BaseTransport | None = None
    ) -> None:
        self._cfg = config
        headers = dict(_HEADERS)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._http = httpx.Client(
            base_url=config.github.api_url, headers=headers, timeout=30, transport=transport
        )
        self.evidence: list[Evidence] = []

    def get(self, path: str, query: dict[str, Any] | None = None) -> Any:
        access = classify_github("GET", path)
        if access is not AccessClass.READ:
            raise OperationDenied(f"GET {path} is {access}; only READ is executed")
        repo = repo_of(path)
        if repo is None:
            raise OperationDenied("only repository-scoped paths (/repos/{owner}/{repo}/...)")
        try:
            self._cfg.repository(repo)
        except ConfigError as exc:
            raise OperationDenied(str(exc)) from exc
        resp = self._http.get(path, params=query or {})
        if len(resp.content) > _MAX_BYTES:
            raise OperationDenied("response too large")
        resp.raise_for_status()
        data = redact(resp.json())
        ev_id = "ev-gh-" + sha256_digest({"path": path, "query": query or {}})[7:19]
        self.evidence.append(
            Evidence(
                id=ev_id,
                source=f"github:GET {path}",
                collected_at=utcnow(),
                summary=f"GET {path} -> {resp.status_code}",
                data=data if isinstance(data, dict) else {"items": data},
            )
        )
        return data


class GitHubAppTokenProvider:
    """Installation tokens limited to one repository and the requested permissions."""

    def __init__(
        self,
        app_id: int,
        installation_id: int,
        private_key_path: Path,
        api_url: str,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._app_id = app_id
        self._installation_id = installation_id
        self._key_path = private_key_path
        self._http = httpx.Client(
            base_url=api_url, headers=_HEADERS, timeout=30, transport=transport
        )

    def _jwt(self) -> str:
        import jwt

        now = int(time.time())
        key = self._key_path.read_text(encoding="utf-8")
        return str(
            jwt.encode(
                {"iat": now - 60, "exp": now + 540, "iss": str(self._app_id)},
                key,
                algorithm="RS256",
            )
        )

    def token_for(self, repository: str, permissions: dict[str, str]) -> str:
        name = repository.split("/", 1)[1]
        resp = self._http.post(
            f"/app/installations/{self._installation_id}/access_tokens",
            headers={"Authorization": f"Bearer {self._jwt()}"},
            json={"repositories": [name], "permissions": permissions},
        )
        resp.raise_for_status()
        return str(resp.json()["token"])


# Minimal installation permissions by path segment (write ops).
_PERMISSION_FOR: tuple[tuple[str, str], ...] = (
    ("/pulls", "pull_requests"),
    ("/issues", "issues"),
    ("/actions", "actions"),
    ("/releases", "contents"),
    ("/contents", "contents"),
    ("/git/", "contents"),
    ("/branches", "contents"),
    ("/deployments", "deployments"),
    ("/environments", "administration"),
)


def permissions_for(path: str) -> dict[str, str]:
    for marker, perm in _PERMISSION_FOR:
        if marker in path:
            return {perm: "write"}
    return {"contents": "read"}


TokenFactory = Callable[[str, dict[str, str]], str]


class GitHubExecutionBroker:
    def __init__(
        self,
        config: AgentConfig,
        settings: AppSettings,
        store: Store,
        token_factory: TokenFactory,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._cfg = config
        self._settings = settings
        self._store = store
        self._tokens = token_factory
        self._policy = PolicyEngine(config)
        self._transport = transport

    def _record(
        self,
        plan: ChangePlan,
        op: PlannedOperation,
        fp: str,
        status: ExecutionStatus,
        reason: str,
        **extra: Any,
    ) -> ExecutionRecord:
        rec = ExecutionRecord(
            task_id=plan.task_id,
            operation_id=op.operation_id,
            plan_digest=plan.digest,
            operation_fingerprint=fp,
            status=status,
            reason=reason[:1000],
            **extra,
        )
        self._store.record_execution(rec)
        outcome = {"SUCCEEDED": "allowed", "BLOCKED": "denied"}.get(status.value, "error")
        self._store.append_audit(
            [
                AuditEvent(
                    actor="github-broker",
                    action=f"github:{op.service} {op.operation}",
                    target=str(op.parameters.get("repository")),
                    outcome=outcome,  # type: ignore[arg-type]
                    correlation_id=plan.task_id,
                    payload_digest=plan.digest,
                    detail=reason[:500],
                )
            ]
        )
        return rec

    def execute(
        self, plan: ChangePlan, op: PlannedOperation, approval_id: str | None = None
    ) -> ExecutionRecord:
        from infra_agent.observability import executions, span

        with span(
            "broker.execute",
            provider="github",
            task_id=plan.task_id,
            operation=f"{op.service}:{op.operation}",
            operation_id=op.operation_id,
        ) as s:
            record = self._execute(plan, op, approval_id)
            s.set_attribute("infra_agent.status", record.status.value)
            if record.external_request_id:
                s.set_attribute("infra_agent.request_id", record.external_request_id)
            executions.add(1, {"provider": "github", "status": record.status.value})
            return record

    def _execute(
        self, plan: ChangePlan, op: PlannedOperation, approval_id: str | None = None
    ) -> ExecutionRecord:
        fp = sha256_digest({"plan": plan.digest, "op": op.model_dump(mode="json")})
        block = ExecutionStatus.BLOCKED
        if op.provider != "github" or op not in plan.operations:
            return self._record(plan, op, fp, block, "not a GitHub operation of this plan")
        existing = self._store.succeeded_execution(fp)
        if existing is not None:
            return existing
        decision = self._policy.evaluate(op, plan.digest)
        if decision.decision is Decision.DENY:
            return self._record(plan, op, fp, block, f"policy: {decision.reason}")
        if self._store.writes_frozen():
            return self._record(plan, op, fp, block, "kill switch: writes are frozen")
        if not (self._cfg.execution.github_writes_enabled and self._settings.enable_github_writes):
            return self._record(plan, op, fp, block, "GitHub writes disabled")
        if decision.decision is Decision.REQUIRE_APPROVAL:
            a = self._store.get_approval(approval_id or "")
            ttl = timedelta(minutes=self._cfg.execution.approval_ttl_minutes)
            if (
                a is None
                or a.plan_digest != plan.digest
                or a.decision != "approved"
                or a.used_at is None
                or op.operation_id not in a.scope
                or utcnow() - a.used_at > ttl
            ):
                return self._record(plan, op, fp, block, "valid consumed approval required")

        repo = str(op.parameters["repository"])
        self._record(plan, op, fp, ExecutionStatus.STARTED, "attempt started")
        try:
            token = self._tokens(repo, permissions_for(op.operation))
            with httpx.Client(
                base_url=self._cfg.github.api_url,
                timeout=30,
                transport=self._transport,
                headers={**_HEADERS, "Authorization": f"Bearer {token}"},
            ) as http:
                resp = http.request(
                    op.service.upper(),
                    op.operation,
                    json=op.parameters.get("body"),
                    params=op.parameters.get("query"),
                )
        except httpx.TransportError as exc:
            return self._record(
                plan,
                op,
                fp,
                ExecutionStatus.AMBIGUOUS,
                f"{type(exc).__name__}: outcome unknown; reconcile before retry",
            )
        except Exception as exc:
            return self._record(
                plan, op, fp, ExecutionStatus.FAILED, f"{type(exc).__name__}: {exc}"
            )
        if resp.status_code >= 400:
            return self._record(
                plan, op, fp, ExecutionStatus.FAILED, f"HTTP {resp.status_code}: {resp.text[:300]}"
            )
        body: Any = resp.json() if resp.content else {}
        return self._record(
            plan,
            op,
            fp,
            ExecutionStatus.SUCCEEDED,
            f"HTTP {resp.status_code}",
            external_request_id=resp.headers.get("x-github-request-id"),
            result=redact(body if isinstance(body, dict) else {"items": body}),
        )


def verify_github(reader: GitHubReader, op: PlannedOperation) -> tuple[bool, str]:
    from infra_agent.execution.verifier import get_path

    spec = op.verify
    if spec is None:
        return False, "no verification spec; outcome unverified"
    try:
        data = reader.get(spec.operation, spec.params or None)
    except Exception as exc:
        return False, f"verification read failed: {type(exc).__name__}: {exc}"
    bad = []
    for path, expected in spec.expect.items():
        try:
            actual = get_path(data, path)
        except KeyError:
            actual = "<missing>"
        if actual != expected:
            bad.append(f"{path}={actual!r} (want {expected!r})")
    return (not bad, "verified" if not bad else "verification failed: " + "; ".join(bad))
