# Implementation Plan

Scope: a **general-purpose** AWS + GitHub operations platform (see `PRODUCT_REQUIREMENTS.md`). No phase may hard-code the core around one AWS service or one repository. Service-specific code (e.g. the ECS diagnosis rules) lives only in optional *playbooks* on top of the generic layers.

Work sequentially. Do not skip security foundations to make an impressive demo. Each phase ends with tests and a recorded demonstration. All cloud mutation tests target a designated disposable sandbox and require explicit operator setup. A failing security test blocks the next phase.

Status legend: `[x]` done and tested, `[~]` partially done, `[ ]` not started.

## Phase 1 — Foundation

- [x] `uv` Python project, Ruff, strict mypy, pytest, `.gitignore` blocking secrets.
- [x] Operator config (TOML) + environment settings with validation: accounts, regions, repositories, environments, budgets, Bedrock model, AWS MCP endpoint. Writes are locked off by type.
- [x] Typed models: `Task` + transition table, `PlannedOperation`, `ChangePlan` + canonical SHA-256 digest, `PolicyDecision`, `ApprovalRequest`, `ExecutionRecord`, `Evidence`, `AuditEvent`.
- [x] Write-disabled `MockExecutionBroker` with no AWS client.
- [x] Bedrock chat model via `langchain-aws` `ChatBedrockConverse`.
- [x] LangGraph foundation: bounded `TRIAGE -> DISCOVER -> ANALYZE -> SUMMARIZE` graph with injected dependencies.
- [x] CLI (`infra-agent`) and minimal FastAPI (`/health/live`, `/health/ready`).
- [x] CI: Ruff, mypy, pytest, gitleaks secret scan.
- [ ] Persistence: PostgreSQL schema + migrations, `langgraph-checkpoint-postgres`; in-memory only in tests.

**Exit:** clean tests; no code path can execute a write; no account data hard-coded; tasks survive restart (pending persistence).

## Phase 2 — Universal read-only integration

- [x] Read identity validation: STS account match, root refusal, IAM policy simulation proving probe mutations are denied.
- [x] Managed AWS MCP adapter (SigV4, read-only identity required, tool allowlist, output redaction, audit of tool calls).
- [x] Generic AWS read executor: any `(service, operation)` validated against botocore service models, classified by a deterministic read classifier + sensitive-read denylist, executed with the read identity and the botocore guard. No per-service tools.
- [ ] GitHub read layer over all repositories the installation/identity can access (official GitHub MCP read-only + REST fallback), with allowlist filtering.
- [x] Example playbook: ECS health diagnosis + GitHub Actions correlation (fixtures).
- [ ] Live verification in a sandbox: a mutating call with the discovery identity is denied by IAM.

- [x] 3-level permission model + DELETE classification (enforcement arrives with the broker).
- [x] Generic LLM read tools (`aws_list_read_operations`, `aws_describe_operation`, `aws_read`) for LangChain `create_agent`.

**Exit:** the agent can inspect any supported AWS service and any authorized repository, read-only, with evidence and zero writes.

## Phase 3 — Autonomous orchestration

- [ ] Supervisor/planner graph that decomposes tasks, routes to AWS / GitHub / verification sub-agents (LangGraph subgraphs, not separate principals).
- [ ] Tool selection from discovered capabilities; bounded steps, retries, checkpoints.
- [ ] Structured plan output (`ChangePlan`) with dependencies; no execution yet.
- [ ] Verification agent that re-reads authoritative state.

**Exit:** multi-step read-only tasks complete across AWS + GitHub; plans for changes are produced but not executed.

## Phase 4 — Secure execution broker

- [ ] Generic boto3 broker: botocore shape validation, server-side account/role resolution, scoped STS credentials, request fingerprints, idempotency, audit.
- [ ] GitHub App installation-scoped write gateway (branch + PR first), respecting branch protection and rulesets.
- [ ] Enable writes one operation class at a time, in a sandbox account, behind policy.
- [ ] Prove the discovery path (AWS MCP, read identity) cannot reach the broker role.

**Exit:** approved/auto-allowed operations execute and are verified; everything else fails closed.

## Phase 5 — Human-in-the-loop

- [ ] Deterministic risk tiers (LOW / MEDIUM / HIGH + prohibited) per operation **and parameters**, default deny.
- [ ] Approval APIs bound to plan digest, scope, actor, expiry; replay and drift protection.
- [ ] LangGraph `interrupt()` / `Command(resume=...)` with persistent checkpoints (LangChain `HumanInTheLoopMiddleware` where it fits).
- [ ] Negative tests: replay, tampering, self-approval, altered scope.

**Exit:** risky changes pause for approval and resume exactly once; denied actions never execute.

## Phase 6 — Event-driven autonomy

- [ ] GitHub signed webhooks, EventBridge -> SQS, CloudWatch alarms; dedupe, leases, DLQ.
- [ ] Scheduled tasks; notifications; background recovery after restart.

**Exit:** events trigger investigations and authorized actions without a chat session.

## Phase 7 — Production readiness

- [ ] AgentCore Runtime deployment, external Postgres/queues, secrets manager, observability, CloudTrail correlation.
- [ ] Dashboard APIs for a React/TypeScript UI; kill switch; runbooks.
- [ ] Security review, failure injection, backup/restore, IAM review before any production write class.

**Exit:** production use only for explicitly approved operation classes.
