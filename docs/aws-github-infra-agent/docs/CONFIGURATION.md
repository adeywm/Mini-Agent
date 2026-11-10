# Configuration and Environment Contract

Use validated Pydantic settings, a server-controlled accounts registry, and environment-specific configuration. No model output, GitHub comment, or HTTP client parameter is allowed to override the IAM role mapping.

## Example settings

| Setting | Purpose | Default policy |
|---|---|---|
| `APP_ENV` | local, test, staging, prod | `local` |
| `LOG_LEVEL` | application logging | `INFO`, secrets redacted |
| `AWS_DEFAULT_REGION` | target region for investigations | `ap-south-1` only if configured |
| `AWS_MCP_ENDPOINT` | **MCP server endpoint region** | official supported endpoint |
| `AWS_PROFILE` | local read-only AWS credentials | no value in deployment; use role |
| `BEDROCK_MODEL_ID` | selected model/inference profile | explicit and validated |
| `BEDROCK_REGION` | inference region | explicit |
| `DATABASE_URL` | metadata and checkpoints | from Secrets Manager/runtime injection |
| `TASK_QUEUE_URL` | SQS queue | optional until event phase |
| `WRITES_ENABLED` | emergency default-deny switch | `false` |
| `SANDBOX_ACCOUNT_ALIAS` | registered account alias | explicit |
| `GITHUB_APP_ID` | GitHub App ID | configured server-side |
| `GITHUB_INSTALLATION_ID` | authorized installation | configured server-side |
| `APPROVAL_TTL_MINUTES` | approval expiry | e.g., 30; operator selects |
| `MAX_GRAPH_STEPS` | planner tool budget | e.g., 20; enforce runtime counter |

Never put GitHub private keys, PATs, static AWS keys, or DB passwords into a checked-in `.env` file. Use deployment secret injection or a secret manager; `.env.example` must contain placeholders only.

## Registry example

```yaml
schema_version: 1
accounts:
  - alias: sandbox
    account_id: "ACCOUNT_ID_FROM_OPERATOR"
    environment: development
    allowed_regions: [ap-south-1]
    discovery_role: "ARN_OF_READ_ROLE_FROM_OPERATOR"
    execution_roles:
      ecs_redeploy: "ARN_OF_SCOPED_ECS_ROLE_FROM_OPERATOR"
    writable_resource_selectors:
      ecs_redeploy: ["tag:agent-managed=true"]
repositories:
  - alias: backend
    owner: "OWNER"
    name: "REPO"
    installation_id: "INSTALLATION_ID_FROM_OPERATOR"
    default_branch: main
writes_enabled: false
```

`ACCOUNT_ID_FROM_OPERATOR` and ARN placeholders are **not usable configuration**. Reject unresolved placeholders at startup in staging/production. Account IDs must be verified with STS on session creation and again at execution. If the target resource has no reliable tag/scope identity, require higher scrutiny or deny.

## Operational modes

- `simulated`: all integrations mocked and writes simulated, suitable for CI and most development.
- `read_only_live`: live investigation only, explicit AWS/GitHub read identities and no write tools available.
- `sandbox_write`: narrowly registered operations in sandbox; requires feature flag, enforcement service role and approval policy.
- `production`: only after formal release gates and isolated deployment identities; never enabled by setting one environment variable alone.

## Configuration validation

On startup: validate known model/region, account registry, role alias mappings, webhook audience/secret, DB connectivity, and policy schema. Fail closed when the policy file is missing, stale, or cannot be parsed. Do not print resolved secrets or session credentials.
