# Event-Driven Autonomy: GitHub Webhooks, EventBridge and SQS

## Event sources

- GitHub `workflow_run`, `check_run`, `deployment_status`, `push`, selected issues/PR events.
- AWS EventBridge rules for ECS deployment state changes, selected CloudWatch alarms, EC2 state changes and other explicit signals.
- Scheduled inventory/read-only health sweeps (opt-in) or authenticated API requests.

## Ingestion pipeline

`Webhook/EventBridge -> validate -> normalize envelope -> durable inbox + dedupe -> queue -> worker -> LangGraph thread -> report/approval/execution -> audit`.

### Normalized event envelope

```json
{
  "provider": "github",
  "event_type": "workflow_run.completed",
  "delivery_id": "opaque-source-delivery-id",
  "repository_alias": "backend",
  "account_alias": "sandbox",
  "timestamp": "2026-11-09T10:00:00Z",
  "correlation_id": "incident-or-deployment-id",
  "payload_ref": "private-object-store-ref"
}
```

`timestamp` is an example, not a live event. Keep raw payloads out of LLM prompts unless filtered and redacted.

## Delivery guarantees

Assume **at-least-once delivery** for webhooks and SQS. Store a unique provider/delivery ID and stable task deduplication key. For unexpected source events, create an investigation-only task. Do not allow repeated alarm transitions to trigger repeated ECS restarts. Limit concurrency per account, service and resource. Provide DLQ, visibility timeout, heartbeat, and dead-letter replay with explicit manual control for mutating workflows.

## Trigger conditions

An event may trigger unattended **investigation**, but must never implicitly authorize arbitrary infrastructure changes. Autonomous remediation requires a separate allowlisted remediation rule with a bounded scope, validated preconditions, permitted IAM principal, cooldown and verification. Most production writes require approval.

## Incident example

1. CloudWatch alarm event arrives.
2. Worker correlates alarm with ECS service and latest GitHub deployment.
3. Read-only AWS/GitHub tools gather evidence.
4. Planner proposes action. If diagnosis confidence is low, report uncertainty and request human review.
5. Policy decides whether to auto-allow, approve, or deny.
6. Broker executes only allowed step; verify new task set and ALB target health.
7. Alert user; if no recovery, escalate rather than loop forever.

## Tests

Invalid signature; wrong repo or AWS account; duplicate delivery; partial AWS alarm event; queue poison message; out-of-order events; repeated action within cooldown; graph crash after event received; replay of an already applied plan.
