# Product Requirements

## Problem

Infrastructure work spans multiple AWS consoles, service-specific APIs, GitHub repositories, CI/CD, audit logs, and change approvals. Operators want an agent that can investigate problems and perform repetitive or multi-step work without repeated manual commands, while preserving control over costly and destructive actions.

## Product vision

A single autonomous agent that accepts user tasks or verified infrastructure events, gathers evidence across AWS + GitHub, creates explicit plans, executes actions in an authorized scope, verifies outcomes, and logs an auditable history.

**Coverage ambition:** capability to access all feasible AWS services and API operations through a generic SDK-backed broker. The platform must not promise that every operation is universally available, safe, or automatable. IAM, resource availability, service-specific requirements, regional support and policy may restrict execution.

## Users

- Phase 1: a single technical operator managing their own development AWS account(s) and known GitHub repositories.
- Later: operations teams with role-based access and approval responsibility.
- Future (not MVP): separate external tenants connecting their own AWS accounts and GitHub installations.

## Required functional capabilities

### Input and event handling
- Accept natural-language task submissions via authenticated HTTP API.
- Ingest validated GitHub webhooks and AWS EventBridge events through a durable queue.
- Deduplicate repeated events and task submissions; associate events with owner, account, resource, and repository.
- Provide task status, progress events, approval requests, and final outcome.

### Investigation
- Access AWS documentation and broad read-only AWS APIs through the managed AWS MCP Server.
- Read repositories, PRs, workflow results, and commits through GitHub MCP.
- Discover service availability for a requested region; check relationships across services.
- Present evidence, confidence, assumptions and any missing data before proposing a change.

### Planning
- Produce explicit, ordered, machine-readable operations with prerequisites, target identifiers, cost/impact notes, verification, and recovery conditions.
- Favor infrastructure-as-code PRs and CI/CD for multi-resource/prod changes.
- Use controlled direct API operations for bounded, reversible maintenance tasks.
- Detect unscoped requests (e.g., 'clean up everything') and refuse automatic action until scope is explicit.

### Execution and approvals
- Support low-risk, policy-approved unattended tasks.
- Require exact-plan human approval for changes above policy risk threshold; hard-block prohibited actions.
- Execute AWS writes only through a structured operation broker under per-task, narrowly scoped credentials.
- Execute GitHub writes through controlled tools/GitHub App credentials with repository allowlists.
- Checkpoint, resume, retry safely, and reject expired or altered approvals.

### Verification and accountability
- Poll for asynchronous completion and independently check health/desired state.
- Persist state changes, approval decision, tool name, operation, resource IDs, error, evidence pointers, and correlation IDs.
- Report partial completion honestly and provide manual recovery instructions when automatic rollback is unsafe.

## MVP scenarios (in order)

**S1 — AWS inventory (read only):** "List unhealthy ECS services in account X, region Y, and explain likely causes." Must not request write credentials or perform mutations.

**S2 — GitHub investigation (read only):** "Compare latest failed deployment with recent commits and GitHub Actions failures." Must link relevant evidence.

**S3 — PR creation (controlled write):** "Create a proposed Terraform change for a development environment." GitHub write requires repository-scope policy, branches/PRs only; no direct main-branch push.

**S4 — Bounded dev remediation:** "Force a new ECS deployment for the approved dev cluster/service." Must request approval unless this exact action/resource combination is explicitly auto-allowed. Verify deployment and health.

**S5 — Event driven:** A validated ECS alarm triggers an investigation, deduplicated incident, and proposal without a chat message. An action is not performed solely because an alarm message instructs it.

**S6 — Pause/resume:** Approve one immutable plan later through API; resume same workflow without duplicate execution. Reject/expiry results in no mutation.

## Non-functional requirements

- **Security:** default deny; isolated credentials; detailed audit; no embedded secrets.
- **Reliability:** recover after restarts, safely handle retries and network failures, avoid duplicate actions.
- **Transparency:** show human-readable changes, target resources, costs/impact, and verification details.
- **Extensibility:** new AWS API operations via registry/introspection; no dedicated agent per AWS service.
- **Observability:** trace IDs across event -> graph -> broker -> AWS/GitHub API -> verification.
- **Cost control:** per-account quotas and estimated change costs; reject unknown-cost auto-execution.
- **Scope:** configurable multiple accounts/regions; no assumption of access from mere discoverability.

## MVP exclusions

- Unattended production deletions, IAM privilege escalation, disabling audit/security controls, arbitrary shell access.
- Unrestricted AWS managed MCP scripts with privileged credentials.
- Public self-serve multi-tenancy; open-ended auto-merge to protected branches.
- Guaranteed automatic rollback for every AWS resource.
- Direct management of secrets' plaintext content.

## Definition of done

All six scenarios pass in sandbox, approval bypass tests fail closed, credentials have demonstrably scoped AWS/GitHub permissions, task history persists across process restarts, and no unapproved live writes occur in testing.
