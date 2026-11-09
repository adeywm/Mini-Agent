# Implementation Plan

Scope: a **general-purpose** AWS + GitHub operations platform (see `PRODUCT_REQUIREMENTS.md`). No phase hard-codes the core around one AWS service or one repository; service-specific code (ECS diagnosis) lives only in optional playbooks.

Status legend: `[x]` implemented and tested (offline / local Postgres), `[ ]` not done. **"Tested" means automated tests with stubbed AWS/GitHub and a real local PostgreSQL. Nothing below has been exercised against a live AWS account, Bedrock or GitHub yet** — see "Live validation" at the end.

## Phase 1 — Foundation
- [x] `uv` project, Ruff, strict mypy, pytest, CI (Ruff, mypy, pytest with Postgres service, gitleaks).
- [x] Operator config (TOML) + env settings with strict validation; writes default off.
- [x] Typed models: `Task` + transition table, `PlannedOperation`, `ChangePlan` + SHA-256 digest, `PolicyDecision`, `ApprovalRequest`, `ExecutionRecord`, `VerificationSpec`, `Evidence`, `AuditEvent`.
- [x] Bedrock via `langchain-aws` `ChatBedrockConverse`.
- [x] PostgreSQL persistence: migrations, append-only audit (DB trigger), single-use approvals, idempotent executions, event dedupe, immutable plans, kill-switch control.
- [x] LangGraph Postgres checkpointer (`AsyncPostgresSaver`).

## Phase 2 — Universal read-only integration
- [x] Generic AWS discovery/validation from botocore models (436 services) + deterministic access classifier (READ / SENSITIVE_READ / WRITE / DELETE / UNKNOWN).
- [x] Generic AWS read executor with in-process guard; read identity proven read-only via IAM policy simulation.
- [x] Managed AWS MCP adapter (SigV4, read identity only, tool allowlist, redaction, audit).
- [x] Generic GitHub REST reader (repository allowlist, sensitive paths refused, redaction).
- [x] Example playbook: ECS diagnosis + GitHub Actions correlation.

## Phase 3 — Autonomous orchestration
- [x] Deep Agents planner (`create_deep_agent`) with AWS/GitHub investigation subagents, read-only tools, virtual filesystem only, structured `PlanProposal` output.
- [x] Operations graph: plan -> policy -> approval -> execute -> verify; server assigns ids/account/environment; dependency ordering; whole-plan denial.
- [x] Read-back verification for AWS and GitHub; unverifiable -> `NEEDS_REVIEW`.

## Phase 4 — Secure execution broker
- [x] AWS broker: plan membership, policy re-check, triple write switch, approval binding, botocore validation, per-call operation guard, idempotency, attempt recorded before call, FAILED vs AMBIGUOUS classification, audit.
- [x] GitHub broker with GitHub App installation tokens scoped to one repository and minimal permissions; protected admin/secret operations prohibited.
- [x] Separate execution identity (`execution_profile`) from the read identity; CloudFormation for read role and permission-bounded execution role (boundary covers every code prohibition, checked by tests).

## Phase 5 — Human-in-the-loop
- [x] Permission levels L1/L2/L3 + risk policy (prohibitions, scope, sensitive reads, public exposure, cost/budget, production never auto).
- [x] Approval service: authorized approver, separation of duties, exact digest, single decision, expiry.
- [x] LangGraph `interrupt()` pause with durable resume across process restarts; the resume payload is not authority (approval consumed atomically from the store).

## Phase 6 — Event-driven autonomy
- [x] Durable Postgres work queue (leases, retries with backoff, dead-letter); crash-safe worker that continues from checkpoints.
- [x] GitHub signed webhooks (HMAC, delivery dedupe) and EventBridge -> SQS ingestion (account/region validation, dedupe, DLQ redrive) mapped by operator triggers.
- [x] Recovery tasks for failed/partial/uncertain tasks (new plan, same controls).

## Phase 7 — Production readiness
- [x] Authenticated API (hashed bearer tokens; roles operator/approver/auditor/admin), task/approval listing for a dashboard, kill switch.
- [x] Container image (non-root) and AgentCore Runtime entrypoint (`/ping`, `/invocations`).
- [ ] React/TypeScript dashboard (the API is ready for it).
- [ ] OpenTelemetry tracing/metrics export and CloudTrail correlation dashboards.
- [ ] External security review and IAM review before enabling any production write class.

## Live validation (operator-run, required before trusting writes)
Requires the operator's sandbox AWS account, Bedrock model access and a GitHub App; none of these were available while building.
1. Deploy `infra/cloudformation/infra-agent.json` to a **sandbox** account; create read / execution / events SSO profiles.
2. `infra-agent check-credentials --account dev` must pass (proves the read role cannot mutate).
3. Run a read-only task end to end with Bedrock + AWS MCP; review the plan and report.
4. Enable exactly one write class (e.g. dev `ecs:UpdateService` on a tagged service) and run an approval round trip; confirm in CloudTrail that only the approved call happened.
5. Exercise the kill switch, a rejection, an expiry and a deliberately failing verification.
