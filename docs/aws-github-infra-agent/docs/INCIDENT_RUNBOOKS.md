# Operational Runbooks

## R1 — Agent proposes unauthorized action

1. Disable write feature flag and disable/deny broker execution identity as appropriate.
2. Stop affected workers and approvals; preserve traces, plan digests, and sanitized payloads.
3. Review CloudTrail, GitHub audit and broker logs for any performed operation.
4. If a live change occurred, perform documented service-specific recovery through a human-operated workflow.
5. Fix policy/test gap and require security signoff before re-enabling writes.

## R2 — Duplicate execution or event replay

1. Identify `delivery_id`, `task_id`, `operation_id`, idempotency key, plan hash, and external request ID.
2. Freeze that resource's operation queue.
3. Reconcile actual AWS state before issuing any new API call.
4. Merge duplicate task reports; never blindly rerun an operation of unknown outcome.
5. Add regression test for replay/concurrency issue.

## R3 — AWS API accepted but deployment unhealthy

1. Preserve deployment ID, ECS events, CloudWatch logs and ALB health checks.
2. Mark task `FAILED` or `NEEDS_REVIEW`, not `COMPLETED`.
3. Follow an explicit rollback plan only if known safe and authorized; otherwise escalate.
4. Compare proposed vs actual resource state and account for partial side effects.

## R4 — IAM or MCP connectivity failure

1. Inspect status of AWS SSO/STS, SigV4 client, MCP endpoint, configured IAM role and region.
2. Verify actual caller identity separately and whether intended API is IAM-authorized.
3. Ensure that failure did not cause a fallback to privileged/default credentials.
4. If auth is unavailable, degrade to documentation-only or queue pending investigation; deny writes.

## R5 — Approval service or PostgreSQL unavailable

1. Freeze execution broker mutations; serving read-only status may continue if safe.
2. Restore DB/approval service; replay pending tasks from durable checkpoint and audited records.
3. Reject stale approvals and re-verify plan/resource state before resuming.
4. Run a known-scope sandbox verification before unfreezing production writes.

## R6 — Runaway cost or tool loop

1. Disable new task ingestion and mutating broker capability.
2. Enforce task cancellation and max-step limits; quarantine events causing repeats.
3. Review Bedrock usage, created AWS resources, AWS Budgets and tagging.
4. Terminate test resources only through approved cleanup workflow after dependency check.

## R7 — Suspected prompt injection

1. Label source (PR, log, issue, docs), isolate task output, and block direct execution.
2. Inspect attempted tool calls and policy denials; preserve a sanitized malicious fixture for tests.
3. Update tool-output redaction/trust labeling, not just the system prompt.
4. Re-run regression suite before allowing the workflow to continue.

## Operational documents to complete before launch

Fill in concrete on-call owner, incident contacts, AWS account ID mappings, CloudWatch dashboards, queue IDs, IAM break-glass process, backup/restore procedure, credential rotation protocol, and verified resource-specific recovery steps. Do not invent these values.
