# AWS and GitHub Integrations

## Core approach

Build a **broad-capability adapter** for AWS API surface discovery and a **separate structured action gateway** for writes. Do not write one hard-coded tool per AWS service.

## AWS managed MCP Server

Verified AWS documentation describes these tools as of November 2026:

- Knowledge: `aws___search_documentation`, `aws___read_documentation`, `aws___retrieve_skill`, `aws___list_regions`, `aws___get_regional_availability`.
- API: `aws___run_script` (sandboxed Python with AWS API access), `aws___get_tasks` (poll long-running script tasks), `aws___get_presigned_url` (S3 pre-signed URL).

Use the official AWS setup guide for OAuth or SigV4 connection. For a headless multi-account backend, prefer SigV4 with dedicated IAM roles and controlled session establishment. Ensure the managed **MCP endpoint region** is supported: as documented in Nov 2026, managed endpoints include `us-east-1`, `us-west-2`, `ap-southeast-1`, `ap-southeast-2`, `ap-northeast-1`, `eu-central-1`, `eu-west-1`, `eu-west-2`. **Mumbai (`ap-south-1`) is not listed as a managed MCP endpoint region in this snapshot.** You can still target resources in `ap-south-1` when your tool/API call permits selecting the target resource region. Do not conflate MCP endpoint region with AWS resource region.

**Security constraint:** The `aws___run_script` interface may contain arbitrary sequences of API calls. Source-level script parsing alone cannot reliably enforce per-call authorization. Connect this adapter **only under IAM credentials that constrain it to investigation/reads** (and necessary documented support actions). If broad read-only access cannot be safely achieved with IAM, restrict more narrowly and report missing coverage.

## Generic AWS write execution broker

LLM proposes **structured data** (not a script):

```json
{
  "provider": "aws",
  "service": "ecs",
  "operation": "UpdateService",
  "account_alias": "dev",
  "region": "ap-south-1",
  "params": {
    "cluster": "dev-cluster",
    "service": "api-service",
    "forceNewDeployment": true
  },
  "expected_resource_ids": ["ecs:dev-cluster/api-service"],
  "reason": "Redeploy after image pull issue is remediated"
}
```

This is a **shape example**, not a pre-approved operation. The broker must:

1. Resolve account alias/role from server-side mapping; never let the agent pick role ARNs directly.
2. Reject unregistered/unknown service, operation, or unknown/malformed botocore input fields; introspect botocore service models to validate request shape.
3. Compute actual target resources and effects when possible; flag generic, account-wide, unbounded or ambiguous operations for mandatory review or deny.
4. Evaluate deterministic policy against normalized service, operation, parameters, account, region, resources, tags, cost, and environment. API names alone are not enough to classify risk.
5. Bind approval to a canonical **entire plan** and execute only approved/auto-allowed steps; re-check immediately before each call.
6. Use temporary credentials and IAM/permission boundaries that independently enforce the allowed scope. Apply explicit deny for forbidden operations.
7. Record request fingerprint and idempotency key; invoke the SDK safely, handle pagination/long-running operations, sanitize response, verify outcome.
8. Require a dedicated adapter for request shapes that cannot be adequately validated, bounded, or verified with the generic operation path (streaming payloads, multipart uploads, some account-level operations, etc.).

The generic broker is a goal of **broad API coverage**, not proof that all APIs are risk-classified. Unknown/unsupported writes fail closed until reviewed and implemented.

## Read vs write operations

Do **not** assume names beginning with `Describe`, `List` or `Get` imply harmlessness in every possible context, nor that SDK models provide an authoritative security classification. Use maintained allowlists, explicit policy rules, and IAM; classify unknowns as `REVIEW_REQUIRED` or `DENY`. Never use a generic `execute_python` tool with a write-capable principal.

## GitHub MCP

Use the official GitHub MCP Server for repository and Actions investigation. Configure read-only mode for the discovery agent (`--read-only` locally, or the server's documented read-only remote option). Configure only necessary toolsets such as `repos`, `issues`, `pull_requests`, `actions`.

For writes, use a GitHub App with installation-scoped access and a gateway that allows chosen repositories and operations. Prefer creating a branch + PR; preserve branch protection, required checks and human review. Treat workflow files and PR comments as untrusted content (prompt injection and CI execution risks).

## Infrastructure-as-code path

For new infra or multi-resource changes, prefer:

`Plan -> IaC branch -> PR -> static checks/policy -> CI plan artifact -> approval -> CI apply under scoped OIDC role -> verify`.

Do not allow the agent to edit a workflow file so as to grant its own elevated CI permissions. GitHub Actions OIDC trust policies must restrict repositories, branches/environments, and audiences as appropriate.

## MCP integration tests

- Tool discovery works and required tool names match the server version.
- AWS discovery attempts to mutate a resource and IAM denies it.
- GitHub MCP read-only disables write operations.
- The broker rejects unknown botocore services/operations/parameters and unsafe resource wildcards.
- A rejected plan cannot be rerun by sending a different equivalent operation to a broad tool.

## Source references

- AWS MCP setup: https://docs.aws.amazon.com/agent-toolkit/latest/userguide/getting-started-aws-mcp-server.html
- AWS MCP tools: https://docs.aws.amazon.com/agent-toolkit/latest/userguide/understanding-mcp-server-tools.html
- AWS MCP proxy: https://github.com/aws/mcp-proxy-for-aws
- GitHub MCP configuration: https://github.com/github/github-mcp-server/blob/main/docs/server-configuration.md
- LangChain MCP adapters: https://github.com/langchain-ai/langchain-mcp-adapters
