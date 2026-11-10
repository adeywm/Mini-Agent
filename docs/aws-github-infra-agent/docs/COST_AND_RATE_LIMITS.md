# Cost, Resource Limits and Performance Guardrails

An autonomous infrastructure agent can incur costs from LLM tokens, AgentCore/compute, databases, logging, queues, MCP/API use, and **the AWS resources it creates**. The last category is potentially much larger than the agent runtime bill.

## Layered limits

1. Per-task LLM budget: maximum prompt/output tokens, max tool calls, max steps and elapsed execution time.
2. Per-account monthly budget: operator-defined max new infrastructure estimate and alarms on actual spend.
3. Concurrency control: max active tasks per account, max simultaneous mutations per resource, maximum AWS API QPS.
4. Action-specific guards: instance family/size limits, service count limits, NAT gateway count, EBS volume sizes, request budget and deployment cooldown.
5. Expenditure uncertainty: if cost cannot be estimated sufficiently, do not allow unattended provisioning.
6. Lifecycle: mandatory owner/cost-center tags, test resource TTL/cleanup policy, post-provision inspection.

## Pricing caveat

Price calculations are region-, date-, usage- and discount-dependent. AWS Pricing APIs do not cover every dimension cleanly; an estimate should have assumptions, confidence, expected run-hours, storage/egress costs and a disclaimer. Financial budget alerts are not a hard transaction-level preventive control. IAM/service quotas and a narrowly scoped executor remain necessary.

## Example operator-defined policy (not a default recommendation)

```yaml
limits:
  max_model_usd_per_task: 2.00
  max_tool_calls_per_task: 30
  max_graph_steps: 20
  max_runtime_minutes: 15
  max_simultaneous_writes_per_resource: 1
  max_estimated_new_monthly_usd_for_auto_approval: 0
  action_cooldown_minutes:
    ecs_redeploy: 30
```

These values are **illustrative** and require operator calibration. In early releases set autonomous provisioning budget to zero and require approval for every real creation.

## Failure mode

If limits are exceeded, stop planning, persist state, and mark `NEEDS_REVIEW`. Never silently increase budgets, switch account, or assume a role with higher quotas to finish the task.
