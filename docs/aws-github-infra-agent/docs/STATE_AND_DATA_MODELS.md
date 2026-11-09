# State and Data Models

Use Pydantic v2 models with strict validation; persist canonical JSON and a SHA-256 digest over the immutable plan representation. Define a stable canonicalization format (normalized key order, numeric semantics, encoding, redaction) and version it. Never hash a rendered human-readable summary as the only source of truth.

## Primary entities

| Entity | Important fields |
|---|---|
| `Task` | id, requester, origin (chat/webhook/event), environment, account aliases, repositories, status, created_at, last_updated |
| `GraphRun` | task_id, thread_id, state_version, checkpoint, active_node, lease_holder, retries |
| `Evidence` | id, task_id, source, timestamp, tool, redacted artifact path, confidence, hash |
| `ChangePlan` | id, version, immutable canonical payload, digest, operation sequence, prerequisites, risk, expected effects, verification |
| `PlannedOperation` | provider, service, operation, account alias, region, sanitized params, resource scope, idempotency, dependencies |
| `PolicyDecision` | plan_digest, operation_id, decision, matching rule ids, reason, required approval level |
| `ApprovalRequest` | id, plan_digest, scope, requester, approver, decision, expiry, used_at, timestamp |
| `ExecutionRecord` | id, task/step/plan, role alias, normalized call hash, attempt, external request id, result, verification, redacted logs |
| `EventInbox` | provider event id, signature/transport verification, dedupe key, task id, received_at |
| `AuditEvent` | actor, action, target, policy, correlation id, payload digest, outcome, immutable timestamp |

## Canonical planned operation example

```json
{
  "schema_version": "1.0",
  "operation_id": "op-001",
  "provider": "aws",
  "account_alias": "dev",
  "environment": "development",
  "service": "ecs",
  "operation": "UpdateService",
  "region": "ap-south-1",
  "resource_scope": ["ecs:dev-cluster/api-service"],
  "parameters": {
    "cluster": "dev-cluster",
    "service": "api-service",
    "forceNewDeployment": true
  },
  "depends_on": [],
  "expected_outcome": "ECS service starts a new deployment",
  "verification": "Service deployment reaches stable state and targets are healthy",
  "estimated_cost_change_usd_monthly": null,
  "idempotency_key": "generated-by-server-not-model"
}
```

The operation object is only a proposal. Actual authorization requires policy + scoped credentials; `resource_scope` is **not self-attesting**. Server-side mapping must confirm its relationship with parameters.

## Task statuses

`RECEIVED`, `TRIAGING`, `INVESTIGATING`, `PLANNING`, `POLICY_REVIEW`, `AWAITING_APPROVAL`, `APPROVED`, `EXECUTING`, `VERIFYING`, `COMPLETED`, `PARTIALLY_COMPLETED`, `DENIED`, `REJECTED`, `EXPIRED`, `FAILED`, `CANCELLED`, `NEEDS_REVIEW`.

Enforce a finite-state transition table; reject invalid transitions with audit entries. A task can contain multiple operations with distinct statuses and approvals. Terminal task state doesn't imply external cloud work is automatically rolled back.

## Deduplication and concurrency

- `task_id` identifies user intent; `event_id` identifies webhook delivery; `operation_id` identifies one plan step.
- `plan_digest` binds approval; `operation_fingerprint` binds normalized API request; broker-generated `idempotency_key` binds one attempted execution.
- Acquire transactional leases/locks before executing writes; persist attempt before calling external API.
- Reconcile AWS/GitHub actual state before retrying after ambiguous network failures.
- A new plan version invalidates previous approval grants.

## Retention

Keep task metadata/audit according to organizational policy; separate raw artifact storage with redaction and lifecycle policies. Avoid storing retrieved secret values. Record reference to CloudTrail/GitHub audit evidence when possible.
