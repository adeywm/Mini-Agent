# Security and Policy Model

## Threat model

The agent reads untrusted inputs (issues, PR comments, code, logs, documents, MCP outputs) and is being asked to operate real infrastructure. Key risks include prompt injection, excessive IAM permissions, incorrect scope, deletion/data loss, cost spikes, accidental public exposure, role escalation, credentials leakage, replayed approvals, webhook spoofing, supply-chain attacks, and bypass of policy via arbitrary scripts/shell.

**Treat every model-generated action as untrusted until validated by a deterministic enforcement service.**

## Security boundaries

1. **LLM sandbox:** no production secrets, privileged cloud credentials, or unrestricted shell.
2. **Discovery tools:** AWS MCP under inspection-scoped IAM, GitHub MCP read-only. Do not rely on prompting or client 'read-only' flags alone for AWS.
3. **Policy decision point (PDP):** decides `ALLOW_AUTO`, `REQUIRE_APPROVAL`, `DENY`, `NEEDS_MORE_DATA` from structured context. The model may suggest risk; it cannot override final risk classification.
4. **Policy enforcement point (PEP):** broker with no bypass route; validates before every write and controls credential access.
5. **Cloud provider guardrails:** IAM trust policy, role permissions, explicit denies, SCPs where available, security controls and CloudTrail logs.
6. **Approval service:** independently authenticates users, checks approver role and exact-plan hash; holds no credentials exposed to the agent.

## Permission levels (what may be requested at all)

Platform permission levels are granted to a user, agent identity, task or connected account by operator configuration, never by chat text. They are **separate from** risk/approval policy and from IAM / GitHub App permissions, which stay authoritative.

| Level | Category | Example AWS | Example GitHub |
|---|---|---|---|
| 1 READ | inspect, list, describe | `DescribeInstances`, `ListBuckets` | read files, PRs, workflow runs |
| 2 CREATE/UPDATE | create or modify | `RunInstances`, `CreateVpc`, `UpdateService` | branches, commits, PRs, workflow edits |
| 3 DELETE | destructive | `TerminateInstances`, `DeleteDBInstance` | delete repos, branches, releases |

Order of checks: **permission level first** (out of level or scope -> deny and audit, approval cannot override), **then** risk policy decides `ALLOW_AUTO` / `REQUIRE_APPROVAL` / `DENY`. Level 3 never implies unattended deletion. Sensitive reads (secret values, credentials, data-plane contents) are not granted by any level and need an explicit operation-specific grant. Implemented in `src/infra_agent/core/permissions.py` (levels) and `src/infra_agent/aws/classifier.py` (READ / SENSITIVE_READ / WRITE / DELETE / UNKNOWN classification for every botocore operation).

## Policy tiers (risk, applied after the permission level)

| Tier | Representative actions | Default behavior |
|---|---|---|
| 0: Read | Inventory, metrics, logs, config inspection | Autonomous with read-scoped credentials |
| 1: Low-risk scoped | Create PR, rerun a dev investigation, update permitted labels | Automatic **only** when explicitly allowed for exact repo/account/environment |
| 2: Mutating | Launch/change instances, alter SG rules, deploy to production, change public DNS | Require approval by default; allow narrow dev exceptions |
| 3: Critical | Delete RDS/S3 data, destroy infrastructure, modify IAM trust/policies, alter networking for production | Mandatory high-assurance approval or default deny |
| 4: Prohibited | Disable audit, exfiltrate secrets, grant itself admin, weaken its own enforcement, bypass approval | Hard deny |

A service is not a risk tier. Classify the **specific operation and parameters**. Even read requests may expose secrets: reading `GetSecretValue` must not be in an ordinary discovery allowlist.

## Suggested policy configuration (illustrative, not executable IAM)

```yaml
policy_version: "1"
default: DENY
allowed_accounts:
  - alias: dev
    environment: development
    allowed_regions: [ap-south-1]
    max_estimated_new_monthly_usd: 20 # sample ONLY; set actual budget with operator
rules:
  - id: inventory
    effect: ALLOW_AUTO
    action_class: READ_PUBLIC_OR_CONFIG_METADATA
    allowed_tools: [aws_discovery, github_discovery]
  - id: dev_ecs_redeploy
    effect: REQUIRE_APPROVAL
    service: ecs
    operation: UpdateService
    account_alias: dev
    resource_tag: "agent-managed=true"
  - id: prod_delete
    effect: DENY
    environment: production
    action_class: DESTRUCTIVE
  - id: privilege_escalation
    effect: DENY
    action_class: PRIVILEGE_ESCALATION
```

The rule engine must specify precise matching semantics; the above is a requirements example, not a ready-to-run policy evaluator. A cost estimate is advisory and must not override IAM or hard-deny rules. Where cost cannot be bounded, disallow unattended spending.

## IAM requirements

- Use isolated, dedicated execution roles per environment and class of task; temporary credentials through STS, not static access keys.
- Explicitly deny IAM escalation (`iam:PassRole`, policy attachment, role modification, role assumption) unless one tightly controlled workflow has a defensible reason and approval.
- Apply permissions boundaries/SCPs or equivalent defense in depth where supported. Protect logging, backups, key management and budget alerts outside the agent's writable scope.
- Map resource ARNs/account IDs from **operator-owned configuration**. Never accept role ARN / account ID from model text as authorization.
- Scope GitHub App installations to repositories. Require protected branches and OIDC trust conditions for deployment jobs.
- AWS MCP-specific IAM condition keys may help scope calls via managed MCP, but are not a complete boundary for direct SDK, CLI or other paths.

## Policy bypass prevention

- Block the agent from calling a write-capable AWS MCP `aws___run_script` directly; privileged mutations use the broker only.
- Broker normalizes operation aliases and evaluates complete normalized params; reject high-risk undocumented side effects.
- No alternative tools (`execute_python`, shell, arbitrary HTTP clients, Docker socket, CI-edit-and-run) with enough privilege to bypass broker checks.
- An approval is valid only for immutable plan digest, designated role/account/region/resources, requester, approver, expiry and limits.
- Re-evaluate for resource drift immediately before execution; changes require a new plan and approval.
- Disallow same principal initiating and approving a production-critical change by default.
- Verify webhook signatures, timestamps and deduplication; reject untrusted event sources.

## Logging and privacy

- Append-only audit records contain who/what/when/where, policy rule, plan digest, operation, approval decision, request ID, outcome, and redacted evidence references.
- Never log AWS access tokens, GitHub installation tokens, decrypted secret values, session cookies or raw credentials.
- Restrict log access and retention. Alert on denied privilege actions and attempted policy bypass.

## Initial production readiness gates

- External IAM review and policy attack tests; cloud account blast-radius review.
- Deny-by-default demonstrated across write-capable tool paths.
- Manual approval UI tested for replay/expiry and tampering.
- Recovery paths documented and tested for partial failure.
- Cost controls, change windows and on-call/kill-switch procedures configured.
