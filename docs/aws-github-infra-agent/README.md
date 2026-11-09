# Autonomous GitHub + AWS Infrastructure Agent

**Status:** Complete project specification and coding-agent handoff (documentation + illustrative configuration); no runnable application code is included.  
**Initial target:** One operator's authorized AWS account(s) and GitHub repositories.  
**Core:** Python, LangGraph/LangChain, Amazon Bedrock, AWS MCP Server (read-only discovery), GitHub MCP Server, policy-controlled AWS/GitHub action gateways, FastAPI, PostgreSQL.  
**Deployment target:** Bedrock AgentCore Runtime after a locally tested MVP.

## What this project does

An event-driven and interactive infrastructure agent that can:

- Investigate AWS resources across services and regions, inspect CloudWatch signals, and correlate incidents with GitHub commits or Actions workflows.
- Plan infrastructure changes; propose Terraform/CloudFormation or GitHub pull requests; execute **authorized** operations against real AWS APIs.
- Automatically complete low-risk operations within an explicitly configured scope and budget, while requesting human approval for higher-risk operations.
- Resume paused workflows, verify outcomes, record evidence, and avoid repeated or duplicate changes.

**Broad capability is not blanket authorization.** The design aims to support the AWS API surface dynamically, including services not individually hard-coded into the agent. Every mutating operation is still subject to explicit policy, AWS IAM, resource scoping, and applicable human approval. Some AWS functionality requires specialized workflows and adapters; 'all services' is a coverage goal, not an assertion of perfect zero-configuration support.

## Documentation map

| File | Purpose |
|---|---|
| `AGENTS.md` | Coding-agent instructions, strict engineering constraints, build order |
| `docs/PRODUCT_REQUIREMENTS.md` | Goals, MVP scenarios, functional/nonfunctional acceptance criteria |
| `docs/ARCHITECTURE.md` | Runtime topology, agents, graph, persistence, failure handling |
| `docs/AWS_AND_GITHUB_INTEGRATIONS.md` | MCP + SDK strategy, read/write separation, tool contract |
| `docs/SECURITY_AND_POLICY.md` | IAM, risk classification, approvals, prompt injection and audit |
| `docs/HUMAN_IN_THE_LOOP.md` | Approval state machine, immutable plans, timeouts and resume |
| `docs/STATE_AND_DATA_MODELS.md` | Persisted records, operation envelopes and statuses |
| `docs/API_AND_EVENTS.md` | API endpoints, webhook triggers, examples |
| `docs/IMPLEMENTATION_PLAN.md` | Phased implementation with checklists and exit criteria |
| `docs/TEST_STRATEGY.md` | Unit, integration, adversarial, chaos and acceptance tests |
| `docs/DEPLOYMENT_AND_OPERATIONS.md` | Local setup, identity, production hardening, runbooks |
| `docs/DECISIONS_AND_REFERENCES.md` | Architecture decisions, verified upstream references |

## Suggested source tree (to be implemented)

```text
.
├── AGENTS.md
├── README.md
├── docs/
├── pyproject.toml
├── .env.example
├── src/infra_agent/
│   ├── api/                 # FastAPI routes, auth, webhook validators
│   ├── graph/               # LangGraph workflow, state and checkpointing
│   ├── agents/              # AWS discovery, GitHub, planner, verifier
│   ├── integrations/        # MCP clients, Bedrock adapter, SDK adapters
│   ├── execution/           # Structured AWS/GitHub action broker
│   ├── policy/              # Decisions, role selection, plan fingerprint
│   ├── persistence/         # Repository and checkpoint adapters
│   ├── events/              # Webhook and EventBridge/SQS handlers
│   └── observability/       # Structured audit, tracing and metrics
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── adversarial/
│   └── e2e/
└── infra/                   # Terraform or CloudFormation, added later
```

## Full expanded documentation

Start with [`docs/START_HERE.md`](docs/START_HERE.md) and [`docs/README_INDEX.md`](docs/README_INDEX.md). The expanded archive additionally contains specifications for the generic AWS execution broker, IAM/STS, LangGraph transitions, GitHub integration, threat model, database DDL, approvals dashboard, event pipelines, IaC/CI, observability, budgets, runbooks, release gates, framework decision, and a ticket-by-ticket coding backlog. Configuration examples are in `examples/` and `.env.example`; **they are deliberately not deployable and contain no credentials**.

**Execution order:** Phase 0 repository/config/tests → read-only AWS/GitHub investigation → durable LangGraph state → deterministic policy/approval → one sandbox write → event automation → staging AgentCore → broaden supported AWS API catalog with reviewed capability adapters.

## How to hand this to Codex or another coding agent

1. Copy this pack into a **new, empty repository**; do not copy AWS secrets or personal tokens.
2. Tell the coding agent: **"Read AGENTS.md and docs/IMPLEMENTATION_PLAN.md. Implement Phase 0 and Phase 1 only. Keep AWS writes disabled. Add tests and provide a report of files changed and verification performed."**
3. Inspect tests and policy boundaries before enabling any mutating capability.
4. Implement one narrowly scoped development-account write operation first; do not enable broad execution based only on a successful demo.

## Essential rules

- Treat AWS accounts, repositories, IAM roles, regions, cost ceilings, and allowed resources as **operator-supplied configuration**. Never hard-code a real account ID or assume permission to use all accounts.
- No production change without a deterministic policy decision; high-risk changes require a valid approval artifact.
- No unrestricted AWS shell, Boto3, or managed AWS MCP write access with privileged credentials from an LLM-accessible path.
- Never place a mutating tool call before a LangGraph interrupt in the same resumable node: the node can replay on resume.
- Verify outcomes independently; an accepted AWS API response is not proof of a healthy deployment.

## Reference documentation

See [`docs/DECISIONS_AND_REFERENCES.md`](docs/DECISIONS_AND_REFERENCES.md) for authoritative links and version-sensitive notes. Verify current service endpoints and SDK methods before implementing.
