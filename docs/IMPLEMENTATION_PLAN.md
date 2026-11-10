# Implementation Plan

Scope: a **general-purpose** AWS + GitHub operations platform (see `PRODUCT_REQUIREMENTS.md`). No phase hard-codes the core around one AWS service or one repository; service-specific code (ECS diagnosis) lives only in optional playbooks.

Status legend: `[x]` implemented and tested (offline / local Postgres), `[ ]` not done. **"Tested" means automated tests with stubbed AWS/GitHub and a real local PostgreSQL.** Live validation (reads, one approved write class, controls drill) against a sandbox account, Bedrock and GitHub is recorded under "Live validation" at the end.

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
- [x] Authenticated API (hashed bearer tokens and PostgreSQL-backed browser sessions; roles operator/approver/auditor/admin), task/approval listing for a dashboard, kill switch.
- [x] Container image (non-root) and AgentCore Runtime entrypoint (`/ping`, `/invocations`).
- [x] React/TypeScript operator dashboard at `/dashboard` (tasks, timeline, exact-plan approval, recovery, kill switch) with strict CSP.
- [x] OpenTelemetry spans/metrics (OTLP when `OTEL_EXPORTER_OTLP_ENDPOINT` is set) and JSON logs with trace ids; AWS request ids on spans for CloudTrail correlation.
- [ ] External security review and IAM review before enabling any production write class.
- [ ] Complete and security-review browser authentication and user management, including creation of separate approvers. Test the full dashboard flow and session lifecycle against a running server.
- [ ] Resolve the execution-safety review findings on retries/concurrency, read-identity proof, execution-account validation, approval expiry and cancellation before relying on live writes.

## Live validation (operator-run, required before trusting writes)
1. [x] Deploy `infra/cloudformation/infra-agent.json` to a **sandbox** account (stack `infra-agent`, ap-south-1); read role profile created.
2. [x] `check-credentials` passes with the read role (15 probe mutations simulated as denied); an admin role is refused.
3. [x] Read-only task end to end with Bedrock (Claude Sonnet 4.5) + managed AWS MCP + GitHub reads: COMPLETED and verified. Every graph node, LLM call and tool call emits a JSON log line and an audit event. Bugs found and fixed live: read guard blocked the SDK's own `sts:AssumeRole`; MCP `metadata` broke SigV4; Bedrock rejected min/max in the native schema (switched to `ToolStrategy`); MCP fallback hid planner errors; planner recursion limit made configurable; `github_get` paths without a leading `/` were refused, causing a planner loop.
4. [x] One write class enabled live: `ssm:PutParameter` on one throwaway parameter (execution role limited to that ARN). Create and update each went Bedrock plan -> REQUIRE_APPROVAL -> second-user approval -> broker on the execution role -> read-back verification: COMPLETED and verified. CloudTrail shows only those approved `PutParameter` calls from `InfraAgentExecution`.
5. [x] Exercise the kill switch, a rejection, an expiry and a deliberately failing verification.
   - [x] Drill run live with all write switches off: the Bedrock planner proposed `ecs:UpdateService` (desiredCount 2) on a real staging service; policy required approval. Refused: self-approval, a wrong plan digest and an expired approval (EXPIRED). Rejection gave REJECTED with no execution. An approved plan was BLOCKED by the kill switch, and separately by the write switches, before any AWS call; resuming again added no execution.
   - [x] Deliberately failing verification after a real approved write gave NEEDS_REVIEW with the mismatch reported.
6. [x] Cleanup: the drill parameter, drill IAM policy and the whole `infra-agent` stack (roles, queues, rule) were deleted afterwards; a tag/IAM/SQS/EventBridge sweep found nothing left. Redeploy the stack to repeat.
