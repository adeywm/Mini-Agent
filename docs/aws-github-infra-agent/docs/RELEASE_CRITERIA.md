# Test Plan and Release Gates

These requirements add executable acceptance gates to `TEST_STRATEGY.md` and `IMPLEMENTATION_PLAN.md`.

## Phase 1 — live investigation gate

- [ ] CloudTrail confirms no write requests attributable to investigation principal.
- [ ] AWS MCP and GitHub MCP connections work with restricted credentials.
- [ ] Attempts to call write APIs fail with IAM/permission denial.
- [ ] One ECS health investigation contains verified resource IDs, timestamps, events and uncertainty.
- [ ] Logs redact tokens/secrets and sensitive data.

## Phase 2 — orchestration gate

- [ ] LangGraph pause, restart, resume and cancellation tests pass.
- [ ] No side effects run before checkpoint/interrupt in the same resumable node.
- [ ] Duplicate, late and out-of-order events do not issue extra writes.
- [ ] Every task has a bounded step count and failure outcome.

## Phase 3 — write broker/HITL gate

- [ ] All security decisions are deterministic and fail closed.
- [ ] A write cannot be triggered directly through AWS MCP, GitHub MCP, shell or arbitrary code tool.
- [ ] Task identity, service, parameters, account and region are fixed to an approved plan hash.
- [ ] Expired/mismatched/forged approval rejected.
- [ ] Repeated write request is idempotently deduplicated or reconciled.
- [ ] Missing audit sink or inability to assume scoped role denies mutation.
- [ ] One safe sandbox ECS redeploy verified end-to-end with postcondition checks.

## Phase 4 — deployment gate

- [ ] Security review of account trust/IAM/SCP and CI trust policies.
- [ ] Runbooks exercised in staging.
- [ ] SQS/DLQ and database backup/restore tested.
- [ ] Cost caps, quotas and alerts configured and verified.
- [ ] Tracing and audit completeness checked.
- [ ] Secrets never leaked to traces, responses or model context.

## Definition of done for every feature

Typed models; authorization and negative tests; complete audit records; operational docs; mocked integration tests; staged least-privilege live test only when applicable; rollback/compensation or explicit non-reversibility disclosure; peer review.

## Prohibited launch claims

Don't claim 'supports every AWS action safely' without a maintained capabilities registry and tested service-specific adapters. Don't claim 'autonomous production recovery' without validated safety, bounded remediation, and verified recovery mechanisms.
