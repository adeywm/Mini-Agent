# Observability, Evaluation and SLOs

## Traces

Correlate every task with trace ID, task ID, graph thread ID, plan ID, operation ID, GitHub delivery ID and AWS request IDs. Use OpenTelemetry-compatible structured spans for graph nodes, MCP tool calls, approval waits, policy checks, broker calls and verification. Never log secrets, raw credentials, token-bearing URLs or sensitive model responses.

## Metrics

| Metric | Why it matters |
|---|---|
| Tasks received/completed/failed/needs review | Correctness and operations |
| Verified successful tasks / all mutating tasks | Real success, not optimistic API success |
| Policy denials by category | Detect bypass attempts and misconfiguration |
| Approval pending age and expiry count | Workflow bottlenecks |
| Duplicate events and deduplication wins | Avoid repeated mutations |
| Broker unknown-outcome and reconciliation count | External API ambiguity |
| AWS API throttle/errors by service | Capacity and reliability |
| Model tokens and Bedrock spend by task | Economic limits |
| Task queue age, DLQ depth, worker concurrency | Event processing health |
| Discovered prompt-injection attempts | Tool-result safety |

## Suggested MVP objectives (targets for discussion, not promises)

- Zero unapproved or out-of-scope AWS writes in test and staging.
- Zero duplicate writes on replay tests.
- 100% of actual writes have a plan digest, matching policy decision, actor and AWS request ID when returned.
- All successful tasks have independent verification and evidence reference.
- Alert on unknown outcomes and policy-service unavailability.

## Quality evaluation dataset

Create 20-40 repeatable cases using sanitized fixtures: ECS service unhealthy, ALB target health mismatch, missing permissions, GitHub Actions failure, deployment image pull failure, S3 access denied, Route53 misconfiguration, repeated CloudWatch alarms, and malicious GitHub text. For each case, score:

- Evidence precision (correct resource and time).
- Correct tool selection and argument validity.
- Diagnosis quality and explicit uncertainty.
- Policy compliance and resource scoping.
- Approval correctness.
- Idempotency and failure recovery.
- Total model/API cost and latency.

Use deterministic assertions for authorization, not LLM-as-judge alone. An evaluator model may grade report usefulness but cannot certify security properties.

## Alerting and audit

Alerts for attempted production-critical actions, failure to assume scoped role, inability to write audit events, unusual read volumes, runaway token usage, unhealthy queue, excessive cost and repeatedly denied operations. Preserve independent CloudTrail and GitHub audit trails. Use an append-only destination for application execution audit.

## Reference

- https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability.html
