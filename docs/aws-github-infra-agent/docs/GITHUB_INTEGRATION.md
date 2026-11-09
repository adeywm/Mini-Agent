# GitHub Integration and Repository Safety

## Goals

- Search/inspect authorized repositories, files, branches, commits, pull requests, issues, Actions runs and deployment workflows.
- Correlate code changes and CI failures with AWS incidents.
- Propose changes through branches and pull requests; avoid direct pushes to protected branches.
- Run approved GitHub workflows without allowing changes to CI definitions that elevate AWS permissions.

## Read path

Use the official GitHub MCP Server with explicit toolsets (`repos`, `pull_requests`, `issues`, `actions`) and read-only mode. It has a hosted endpoint and self-hosted implementation; choose based on organizational policy and credential handling. No GitHub issue, README, pull request text or comment may override agent policy or system instructions.

Read-only capability must be enforced by MCP configuration **and GitHub token permissions**, not just prompts. Inspect content for secrets before placing in LLM context.

## Write path

Use a GitHub App, not an all-repositories personal access token. App installation and repository allowlists are maintained server-side. A dedicated GitHub broker verifies exact repo, target branch, intended files, plan digest and allowed operation before issuing an installation token. Prefer:

1. Create branch from expected base SHA.
2. Commit patch with explicit path allowlist; reject symlinks/submodules/path traversal and secrets.
3. Open PR with test results and a link to the infrastructure change plan.
4. Require branch protection and human review for protected paths and workflow changes.
5. Wait for required checks before merge/deployment. No agent ability to bypass checks or self-approve protected PRs.

## High-risk GitHub paths

- `.github/workflows/**`, workflow actions, OIDC assumptions, artifact download-and-execute steps.
- Terraform/CDK policies, IAM roles, KMS/CloudTrail/Secrets Manager settings.
- `AGENTS.md` or other instruction files: treat modifications as code review events, never automatically trust changed rules.
- Dependency lockfiles and scripts that alter build-time execution.

## Webhooks

Authenticate with `X-Hub-Signature-256` HMAC using constant-time comparison; persist `X-GitHub-Delivery` ID to deduplicate. Validate event type, repo installation/ownership, expected workflow and target branch. Immediately enqueue and acknowledge; avoid heavy processing within the webhook HTTP request. Use replay-resistant, time-bounded event handling and no privileged GitHub secret in logs.

## Event-to-task example

- Trigger: `workflow_run.completed` where conclusion is `failure` on registered repository/environment.
- Fetch workflow logs and last successful commit; correlate deployment ID/tags with ECS service events.
- Produce a report and open an issue/PR **only if policy authorizes it**. Do not automatically retry a failed deployment if IAM or infrastructure settings may be wrong.

## Official references

- https://github.com/github/github-mcp-server
- https://github.com/github/github-mcp-server/blob/main/docs/server-configuration.md
- https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app
- https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries
- https://docs.github.com/en/actions/security-for-github-actions/security-hardening-your-deployments/about-security-hardening-with-openid-connect
