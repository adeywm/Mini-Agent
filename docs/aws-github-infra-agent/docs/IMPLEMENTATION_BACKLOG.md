# Codex-Ready Implementation Backlog

Use this as a sequential set of engineering tickets. Each should be delivered as a reviewed PR with tests and a verification summary. Do not combine all tickets into one generated monolith.

| ID | Title | Depends on | Acceptance |
|---|---|---|---|
| P0-01 | Initialize typed Python + uv repo | — | Package imports, CI quality checks green |
| P0-02 | Pydantic config and account registry | P0-01 | Unknown account/region refused; no secrets printed |
| P0-03 | Task/operation/approval typed models | P0-01 | Strict validation, immutable plan digest tests |
| P0-04 | Simulated AWS/GitHub service fixtures | P0-02 | Zero network requirement for CI |
| P1-01 | Read-only AWS MCP adapter | P0-04 | Tool discovery and healthy ECS query with read role |
| P1-02 | Evidence normalizer/redaction | P1-01 | Resource IDs + time + provenance; secret test passes |
| P2-01 | GitHub MCP read-only adapter | P0-04 | Inspect commit and failed Actions run |
| P2-02 | Incident correlation report | P1-02,P2-01 | Evidence-based report with uncertainty |
| P3-01 | LangGraph state and Postgres checkpoint | P0-03 | Crash/restart/resume tests pass |
| P3-02 | Graph nodes + bounded planner | P3-01 | Side-effect free planning, bounded loops |
| P4-01 | Policy registry and decision engine | P0-03 | Deny all unknown writes; exhaustive negative cases |
| P4-02 | STS credential broker | P4-01 | Role mappings trusted; no broad fallback |
| P4-03 | Structured AWS broker and idempotency | P4-02 | Reject malformed/out-of-scope operations |
| P5-01 | Approval store and authenticated API | P3-01,P4-01 | Expiry/replay/changed plan denied |
| P5-02 | LangGraph interrupt/resume | P5-01 | Paused task resumes only after valid approval |
| P6-01 | Sandbox ECS redeploy capability | P4-03,P5-02 | Scoped role, exact service, approval and verification |
| P6-02 | Outcome reconciliation + fault recovery | P6-01 | Timeout/unknown outcome doesn't blindly retry |
| P7-01 | GitHub webhook verification and dedupe | P2-01 | Forged/duplicate deliveries denied |
| P7-02 | EventBridge/SQS ingestion and DLQ | P7-01 | At-least-once replay tests pass |
| P8-01 | OTEL traces + audit sinks | P6-01 | Trace linkage and redaction proven |
| P8-02 | AgentCore staging deployment | P8-01 | Live read-only staging and successful recovery from restart |
| P9-01 | Terraform/CloudFormation PR workflow | P8-02 | CI plan, approval binding, immutable artifact and protected apply |
| P9-02 | Capability catalog expansion | P9-01 | Reviewed, tested per-operation support |

## Ticket template

**Context:** link docs and current phase. **Implementation:** smallest code change. **Safety:** new privileges, mutating paths and bypass concerns. **Tests:** positive, negative, replay and failure cases. **Acceptance:** demonstrable output with logs redacted. **Rollback:** deployment/feature-flag plan. **Open risks:** known limitations.

## First Codex prompt

> Implement tickets P0-01 through P0-04 and P1-01 in simulation mode. Read `AGENTS.md` and all linked docs before coding. Create Python 3.12+ package with uv, typed contracts, config validation, fake AWS/GitHub adapters, and unit tests. Provide run commands. No real AWS write credentials, no live mutations, and no production IAM changes. Report tests executed and outstanding risks.
