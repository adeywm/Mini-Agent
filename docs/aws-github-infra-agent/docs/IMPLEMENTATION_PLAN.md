# Implementation Plan

Work sequentially. Do not skip security foundations to make an impressive demo. Each phase ends with tests and a recorded demonstration. All cloud mutation tests target a designated disposable sandbox and require explicit operator setup.

## Phase 0 — Repository and safeguards

- [ ] Initialize `uv` Python project, lint/type/test tooling, minimal FastAPI, environment loader.
- [ ] Add `.env.example` with placeholders and `.gitignore` preventing secrets/credentials commits.
- [ ] Define configuration (`accounts`, `regions`, `allowed_repos`, `environments`, `budgets`, `model`, `MCP endpoint`).
- [ ] Implement typed task/plan/approval/policy models from `STATE_AND_DATA_MODELS.md`.
- [ ] Build a no-op `MockExecutionBroker`; default write ability to **disabled**.
- [ ] Add CI for Ruff, static typing, pytest, secret scan.

**Exit:** clean tests; a model cannot execute a real write; no account data hard-coded.

## Phase 1 — Read-only investigation POC

- [ ] Connect Amazon Bedrock model through LangChain, based on current supported SDK APIs.
- [ ] Implement LangGraph workflow `TRIAGE -> DISCOVER -> SUMMARIZE` with per-task bounded steps.
- [ ] Integrate managed AWS MCP via SigV4 read-scoped role; verify actual account and region scope.
- [ ] Integrate official GitHub MCP in read-only mode.
- [ ] Implement S1 and S2: ECS health diagnosis and GitHub Actions correlation.
- [ ] Redact AWS/GitHub MCP output before model context/logging.
- [ ] Test a deliberately denied mutating call using AWS discovery credentials in sandbox.

**Exit:** reproducible read-only incident report with source evidence and **zero writes**.

## Phase 2 — Durable workflow + event triggers

- [ ] Add PostgreSQL, migrations, persistent LangGraph checkpointer.
- [ ] Implement task status API, stable thread IDs, evidence references.
- [ ] Implement GitHub signed webhook -> queue and EventBridge/SQS ingestion with dedupe.
- [ ] Add execution leases, bounded retries, DLQ and restart/resume integration tests.

**Exit:** the same investigation can be triggered by events and survives a restart.

## Phase 3 — Policy engine and HITL

- [ ] Implement deterministic risk rules with default `DENY` and separate policy decision point.
- [ ] Produce canonical structured plans with strict validation and SHA-256 digests.
- [ ] Implement approval APIs, RBAC, expiration, immutable plan details, reason and audit.
- [ ] Implement LangGraph `interrupt()` and `Command(resume=...)` integration with persistent checkpointing.
- [ ] Add negative tests for replay, drift, tampering, self-approval and altered scope.

**Exit:** plan approval works but live writes **remain disabled**.

## Phase 4 — Controlled GitHub write and dev AWS change

- [ ] Add GitHub App installation-scoped write gateway: branch + PR only for allowed repositories.
- [ ] Add Boto3 structured broker with botocore API shape validation and policy enforcement.
- [ ] Add server-side role resolver, scoped STS credentials, request fingerprints and audit.
- [ ] Enable exactly one dev ECS operation (`UpdateService` for tagged/allowlisted service) behind policy.
- [ ] Implement post-action health verification, ambiguity reconciliation and deduplicated retry handling.
- [ ] Verify that the MCP discovery path cannot invoke this write or assume the broker role.

**Exit:** S3/S4/S6 demonstrated in sandbox, with accepted and rejected approvals; protected resources untouched.

## Phase 5 — Broader AWS support + IaC

- [ ] Add service/operation model introspection and validation without defaulting new writes to allowed.
- [ ] Add policy packs for selected additional services (EC2, VPC, S3, ALB, RDS) one at a time.
- [ ] Add IaC PR/plan/apply path with GitHub Actions and scoped OIDC deployment role.
- [ ] Build account-wide cost/quotas checks, resource drift preconditions, execution receipts.
- [ ] Build policy test vectors for every newly supported operation class.

**Exit:** a new supported service can be onboarded without a new 'AWS agent', while permissions remain bounded.

## Phase 6 — AgentCore deployment / production readiness

- [ ] Package and deploy to AgentCore Runtime with managed identity and network boundaries.
- [ ] Configure persistent database, worker orchestration, metrics, CloudTrail correlation and alerts.
- [ ] Add audit dashboard, approval UI, operator kill switch and runbooks.
- [ ] Perform negative-security review, failure injection, backup/recovery testing and on-call handoff.
- [ ] Gate production change enablement on security sign-off and scoped IAM review.

**Exit:** production use only for explicitly approved operation classes, not 'all actions unrestricted'.

## Coding agent's first task

"Read `AGENTS.md` and all documents. Implement **Phase 0 and Phase 1**, but configure AWS/GitHub clients only after mocked tests are passing. Keep mutations disabled. Build a CLI or HTTP demonstration that investigates an explicitly configured dev ECS service and correlates GitHub Actions. Return an exact test and security report."
