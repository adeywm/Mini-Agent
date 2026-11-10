# Deployment and Operations

## Development setup (after Phase 0 code exists)

Suggested tooling: Python, `uv`, AWS CLI v2, a configured AWS IAM Identity Center profile, Docker/PostgreSQL for local services. Use an entirely separate development AWS account or a tightly scoped disposable environment. These commands are examples of the *intended* workflow once code is implemented:

```bash
uv sync
uv run pytest
uv run ruff check .
aws sso login --profile infra-agent-readonly-dev
# Later, after implementing server:
# uv run uvicorn infra_agent.api.main:app --reload
```

Do not assume those commands run now: this deliverable contains **docs only**.

## Suggested environment configuration (placeholders)

```dotenv
APP_ENV=local
AWS_PROFILE=infra-agent-readonly-dev
AWS_MCP_ENDPOINT=https://aws-mcp.us-east-1.api.aws/mcp
AWS_MCP_ENDPOINT_REGION=us-east-1
DEFAULT_TARGET_REGION=ap-south-1
BEDROCK_REGION=us-east-1
BEDROCK_MODEL_ID=<operator-enabled-model-id>
DATABASE_URL=postgresql+psycopg://<user>:<password>@localhost:5432/infra_agent
GITHUB_APP_ID=<github-app-id>
GITHUB_APP_PRIVATE_KEY_SECRET_ID=<secret-reference-not-inline-key>
GITHUB_WEBHOOK_SECRET_ID=<secret-reference>
ENABLE_AWS_WRITES=false
ENABLE_SANDBOX_WRITES=false
```

The MCP endpoint region and target AWS resource region are separate values. As of the current AWS setup guide, Mumbai is not listed as an AWS managed MCP **endpoint**; an `ap-south-1` resource can still be targeted via a supported endpoint's relevant API calls if authorized.

## Production deployment outline

- Deploy FastAPI gateway + graph worker using Bedrock AgentCore Runtime where appropriate, with documented invocation contract and scaling model.
- Place durable PostgreSQL and message queue outside ephemeral execution processes.
- Require short-lived authentication, distinct execution roles, strict IAM trust relationships and secret manager access.
- Provide outbound access only to needed AWS MCP, GitHub, Bedrock and operational endpoints; protect internal broker endpoint.
- Deploy through a CI pipeline with branch protection, security scans, IaC review, artifact provenance and staged promotion.
- Correlate AgentCore runtime traces with CloudTrail events and GitHub deployment run IDs.
- Encrypt databases, queues and artifacts, and configure backups and tested restore procedures.

## Operations runbooks

### Kill switch

- Set policy gate to deny all new writes and disable issue-triggered mutations.
- Revoke/disable agent write roles/GitHub App permissions or deployment environment as necessary.
- Preserve audit, pending plans and last-known resource state for investigation.
- A kill switch cannot undo external operations already accepted by AWS/GitHub.

### Tool outage

- Mark task retryable, use bounded backoff and DLQ; do not switch to an unauthorized alternate tool.
- Surface partial evidence and limits; do not invent observations.

### Credential failure

- Stop operation; diagnose which scoped role failed; do not retry as AdministratorAccess.
- Rotate/revoke exposed credentials immediately and investigate audit trails.

### Partial deployment

- Inspect actual state, reconcile executed operations, and assess safe recovery. Require approval for recovery actions of equal or greater risk. Do not automatically delete resources simply because a later step failed.

### Approval backlog

- Expire old requests, notify approvers, re-plan after drift, and fail closed.

## Monitoring

Track: active task count, queue age, approvals pending/expired, policy denials by reason, action attempts, reconciliation failures, verification failures, API throttling, execution spending, model/tool latency, token use and unexpected cross-account requests.

## Production readiness checklist

- [ ] All write tools brokered and evaluated under scoped IAM.
- [ ] Exact plan digest, approval expiry/replay protections verified.
- [ ] External webhook sources authenticated.
- [ ] Real sandbox tests performed and reviewed.
- [ ] Audit logs and CloudTrail correlations accessible.
- [ ] Kill switch and disaster recovery tested.
- [ ] Resource/cost quotas and alerting enabled.
- [ ] No live secrets in repository, prompts, checkpoints, or logs.
