# API and Event Contracts

These contracts are a starting design, not implemented routes. Require authentication and role-based authorization for every endpoint except verified inbound webhooks; do not expose production mutation endpoints publicly without network and identity controls.

## REST API

| Method / path | Behavior |
|---|---|
| `POST /v1/tasks` | Submit intent + account/repository scope + optional desired deadline |
| `GET /v1/tasks/{task_id}` | Task status, timeline, referenced plans and redacted findings |
| `GET /v1/tasks/{task_id}/events` | Paginated execution/audit event stream |
| `GET /v1/tasks/{task_id}/plan` | Immutable proposed plan + digest + approvals required |
| `POST /v1/tasks/{task_id}/cancel` | Stop future steps; no promise of rollback |
| `GET /v1/approvals/{approval_id}` | Human-readable exact-plan review with policy reasoning |
| `POST /v1/approvals/{approval_id}/decision` | Approve/reject/request-changes with explicit digest |
| `POST /v1/webhooks/github` | GitHub webhook: verify HMAC signature and delivery ID, enqueue |
| `GET /health/live` | Process liveness; no external dependency check |
| `GET /health/ready` | Dependency readiness with safe redaction |

## Submit task example

```http
POST /v1/tasks
Content-Type: application/json
Authorization: Bearer <short-lived-user-token>

{
  "intent": "Investigate failing ECS deployment and propose a fix",
  "account_alias": "dev",
  "region": "ap-south-1",
  "repository": "owner/backend-repo",
  "execution_mode": "policy_controlled"
}
```

Return `202 Accepted` with a server-generated task ID; actual execution occurs asynchronously through an internal work queue. The API must not accept a client-supplied IAM role ARN as a permission grant.

## Approval example

```http
POST /v1/approvals/apr-123/decision
Content-Type: application/json
Authorization: Bearer <approver-token>

{
  "decision": "approve",
  "plan_digest": "sha256:<canonical-immutable-plan-hash>",
  "reason": "Approved for the configured dev account"
}
```

The approval service validates the authenticated actor's access, the plan digest, exact scope, expiry and one-time use before resuming the graph. The executor independently revalidates.

## Events

### GitHub

- Verify `X-Hub-Signature-256` using stored webhook secret; validate delivery ID and selected event type.
- Subscribe narrowly to `workflow_run`, `check_run`, `pull_request`, or other required types.
- Treat event payload and repo content as data, never tool instructions.
- Queue with deduplication; do not perform cloud changes directly in webhook handler.

### AWS

- EventBridge captures alarms/state changes; route to SQS and a bounded worker.
- Configure event patterns for specific accounts, resource types and environments.
- Validate source and original AWS event account/region; apply per-account policy after ingestion.
- Retry with exponential backoff, bounded receive counts and DLQ; alert on poison events.

## Streaming progress (optional after MVP)

Provide SSE or WebSockets from the authenticated API for task progress. Events may be out of order across async workers: include monotonically increasing sequence numbers and store canonical history.

## Authentication and abuse controls

- Use verified OIDC/OAuth identity for humans; per-operator RBAC and MFA for critical approvals.
- Rate-limit submissions, cap concurrency per account, bound tokens/tool iterations and timeouts.
- Validate request payload schema and maximum sizes; prevent request-supplied path/URL/ARN traversal.
- Use separate internal authentication between API, graph workers, policy service and execution broker.
