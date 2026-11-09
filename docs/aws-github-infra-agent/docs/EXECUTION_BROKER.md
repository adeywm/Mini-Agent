# General-Purpose AWS Execution Broker Specification

This is the security-critical service between untrusted agent proposals and privileged AWS APIs. It is **not** a generic `execute_python` tool. No agent-available tool can call AWS with more privileges than its investigation role except through this broker.

## Input contract

```json
{
  "schema_version": 1,
  "task_id": "task-123",
  "plan_id": "plan-123",
  "plan_hash": "sha256:<canonical-plan-digest>",
  "operation_id": "op-456",
  "account_alias": "sandbox",
  "region": "ap-south-1",
  "provider": "aws",
  "service": "ecs",
  "operation": "UpdateService",
  "parameters": {
    "cluster": "dev-cluster",
    "service": "api-service",
    "forceNewDeployment": true
  },
  "preconditions": [{"type": "resource_tag", "key": "agent-managed", "value": "true"}],
  "postconditions": [{"type": "ecs_service_stable", "cluster": "dev-cluster", "service": "api-service"}],
  "approval_id": "approval-from-server-or-null",
  "idempotency_key": "task-123/op-456/v1"
}
```

Example only. Caller-supplied `approval_id` is a reference to a server record; it is **not** authority. Use canonical serialization and a versioned schema; reject unknown fields on security-bearing models.

## Execution algorithm (mandatory order)

1. Authenticate the caller as the *agent runtime service*, not the human approver. Authorize its access to this task.
2. Resolve `task_id`, `plan_id`, account alias, operation and plan hash against persisted server records. Reject unsolicited operations.
3. Validate the service/operation against pinned botocore models. Validate payload structure, types, length and scope. Map botocore service names carefully; do not assume AWS API CamelCase name equals SDK snake_case name.
4. Extract resource scope and side effects. Some APIs cannot be safely resource-bound; deny or route to a custom reviewed adapter.
5. Check maintenance windows, approved resource tags, environment, cost/quota budget, dependency order and hard-deny actions.
6. Evaluate deterministic policy. If approval required: verify authenticated approver, plan hash, operation set, resources, expiry, risk acceptance and separation of duties.
7. Re-read critical live state (including account via STS and target preconditions) to detect drift. Reauthorize if changed.
8. Get a narrowly scoped STS session from a trusted credential broker. The agent never receives raw credentials or role ARNs.
9. Atomically register pending/in-progress execution using the idempotency key before any remote effect. On duplicate, return known state or start reconciliation—never blindly repeat.
10. Call Boto3 once with sanitized arguments, timeouts and an explicit region. Handle AWS service throttles with bounded backoff and idempotency protections.
11. Persist response metadata, AWS request ID, principal, resource ID and verified outcomes. Never persist secrets or raw potentially sensitive bodies.
12. Verify independent postconditions through discovery. Mark `COMPLETED`, `FAILED`, `NEEDS_REVIEW` or `COMPENSATING` as appropriate.

## Risk and capability classification

The service model tells you what input shape exists; **it does not prove that an operation is read-only or safe**. Maintain a reviewed rule/catalog for supported writes. Unclassified AWS APIs cannot be auto-executed even if Boto3 can call them. Prefer Terraform/CloudFormation workflow for complex create/update/delete sequences.

## Supported execution classes

| Class | Example | Requirement |
|---|---|---|
| Read/investigation | Describe ECS services | Scoped read credentials; redact responses |
| Bounded dev modification | ECS force-new-deployment | Service allowlist, exact scope, approval, verification |
| New infrastructure | VPC + ALB + ECS | IaC plan + policy checks + CI-controlled apply |
| High impact | RDS deletion, backup removal | Deny by default, independent privileged workflow if ever supported |
| Security sensitive | IAM role escalation, KMS key policy change | Deny by default; separate formal security change process |

## Failure behavior

- Throttling: retries only for safe/idempotent calls with bounded jitter.
- Network timeout after submission: `UNKNOWN_OUTCOME` then reconcile, not immediate retry.
- Partial multi-step success: record every step and execute only designed compensating actions; never assume rollback exists.
- Plan drift: stop and request re-plan/re-approval.
- Policy engine or audit sink unavailable: **fail closed** for writes.

## API surface coverage strategy

Maintain a capability catalog built from botocore service models plus an actively reviewed operation registry with resource resolvers, guardrails, output redaction and verifier plugins. Generate docs/tests for catalog entries. **Any undocumented, unknown, or untested write is not automatically supported.** This is how to pursue wide AWS service support without pretending a single generic tool makes all APIs safe.

## Essential negative tests

- Unknown operation, service, field, resource, account or region.
- Role escalation attempted by changing plan JSON.
- Approval replay for a changed region or parameter.
- Two workers executing same operation concurrently.
- AWS timeout after potentially successful create.
- MCP or shell bypass of broker.
- Log payload containing a secret returned by an AWS API.
