# Full Documentation Index

## Read in this order

1. `START_HERE.md` — implementation sequence and what working looks like.
2. `PRODUCT_REQUIREMENTS.md` — user journeys, requirements and non-goals.
3. `ARCHITECTURE.md` and `GRAPH_AND_AGENT_DESIGN.md` — services, orchestration and state transitions.
4. `AWS_AND_GITHUB_INTEGRATIONS.md` and `GITHUB_INTEGRATION.md` — discovery and controlled writes.
5. `SECURITY_AND_POLICY.md`, `IAM_AND_CREDENTIAL_BROKER.md`, `THREAT_MODEL.md` — trust and permissions.
6. `EXECUTION_BROKER.md`, `HUMAN_IN_THE_LOOP.md`, `APPROVAL_DASHBOARD.md` — execution safety.
7. `STATE_AND_DATA_MODELS.md`, `DATABASE_SCHEMA.md`, `API_AND_EVENTS.md`, `EVENT_AUTOMATION.md` — persistence and external interfaces.
8. `IAC_AND_CICD.md`, `CONFIGURATION.md`, `COST_AND_RATE_LIMITS.md` — implementation boundaries.
9. `TEST_STRATEGY.md`, `OBSERVABILITY_EVALUATION.md`, `RELEASE_CRITERIA.md` — test and quality bar.
10. `DEPLOYMENT_AND_OPERATIONS.md`, `INCIDENT_RUNBOOKS.md` — production operations.
11. `IMPLEMENTATION_PLAN.md`, `IMPLEMENTATION_BACKLOG.md` — execution order and individual tasks.
12. `LANGUAGE_AND_FRAMEWORK_DECISION.md`, `DECISIONS_AND_REFERENCES.md` — rationale and official upstream sources.

## Project control files

- `../AGENTS.md`: coding-agent instructions and non-negotiable constraints.
- `../README.md`: project summary and documentation access.
- `../.env.example`: non-secret, placeholder-only settings.
- `../examples/account-registry.example.yaml`: trusted target map, invalid until operator fills values.
- `../examples/policy.example.yaml`: sample policy *requirements*, not an executable policy engine.
- `../examples/operation.example.json`: proposed operation shape; never permission to execute.
- `../examples/approval.example.json`: immutable-plan approval artifact shape.

## Definition of “full documentation” here

This archive is a complete **project specification and coding-agent handoff**, not a finished backend/frontend, IaC deployment, valid IAM policy, or live AWS credential bundle. Some requirements intentionally remain operator-specific (AWS account IDs, GitHub repo, model ID, region, spending caps, approvers, RBAC, role ARNs). The implementation should enforce their presence instead of guessing.
