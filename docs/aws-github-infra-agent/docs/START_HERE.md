# Start Here: from empty repo to safe autonomous infrastructure agent

**Audience:** implementing engineer and coding agent. **Stack:** Python 3.12+, `uv`, LangChain + LangGraph, Bedrock, AWS MCP (read-only exploration), GitHub MCP (read-only exploration), Boto3 execution broker, FastAPI, PostgreSQL. **Version note:** commands and SDK APIs must be checked against pinned versions before use.

## Definition of success

1. An authorized operator submits `Investigate why ECS service X is unhealthy`.
2. The system identifies the target account and region from trusted configuration, obtains credentials with only the required read privileges, and inspects ECS, CloudWatch, target groups and GitHub Actions.
3. The agent cites resource IDs, events, timestamps, and repository evidence. It never fabricates a successful diagnosis.
4. For a permitted development-environment change, the planner emits a structured proposal; a **separate** policy engine and execution broker decide whether it may run.
5. For risky changes, a real authenticated human approves a *specific immutable plan*. LangGraph resumes from durable state, and the broker verifies policy again before calling AWS.
6. Audit data shows the final outcome; if uncertain, the task is `NEEDS_REVIEW`, not `COMPLETED`.

## In-scope first release

- One operator, one isolated AWS sandbox account, one allowlisted GitHub repository.
- One interaction mode: authenticated API with CLI or simple dashboard.
- One automated trigger: GitHub Actions failure or selected CloudWatch/EventBridge event.
- Read-only AWS + GitHub investigation; generate incident reports with evidence.
- First write: a tightly bounded **development ECS force-new-deployment**, only after exact-plan approval, independently checked by IAM, and verified afterward.

## Out of scope first release

- AdministratorAccess, unbounded all-service writes, production self-modification, arbitrary shell/code execution with write credentials, multi-tenant billing, autonomous deletion of databases, changing IAM trust relationships, and automatic changes to deployment workflow security policies.

## Phase-by-phase build order

| Phase | Build | Exit gate |
|---|---|---|
| 0 | Repository, `uv`, config, typed models, tests, no network | `pytest`, Ruff and type check green |
| 1 | Read-only AWS inventory via mocked Boto3/MCP, then real limited account | Deliberate mutation denied by IAM |
| 2 | GitHub read-only inspection and evidence normalization | Tool outputs treated as untrusted data |
| 3 | LangGraph planner/state/checkpointer, deterministic routing | Resume/retry tests pass without duplicated side effects |
| 4 | Operation broker with Boto3 model validation and policy engine | Deny-by-default and adversarial bypass tests pass |
| 5 | Approval UI/API, immutable plan digest, approval replay protection | Modified/expired approval rejected |
| 6 | One allowlisted ECS write and independent verification | Sandbox smoke test with rollback/incident report |
| 7 | EventBridge/SQS and GitHub webhook triggers, deduplication | Event replay does not duplicate writes |
| 8 | AgentCore packaging, observability, operational controls | Staging soak and security review |
| 9+ | IaC plans and additional services via adapters | Per-operation supported-capabilities matrix and tests |

## Developer bootstrap (no live writes)

```bash
uv init infra-agent --package
cd infra-agent
uv add langchain langgraph langchain-aws langchain-mcp-adapters mcp boto3 botocore fastapi 'uvicorn[standard]' pydantic pydantic-settings sqlalchemy psycopg[binary] langgraph-checkpoint-postgres
uv add --dev pytest pytest-asyncio ruff mypy httpx
# Copy the docs pack into the repository, then follow AGENTS.md.
uv run pytest
uv run ruff check .
```

The above is a dependency/bootstrap suggestion, **not a runnable agent application**. Implement the package incrementally and commit a lockfile. Validate import paths and current SDK versions in the local environment.

### AWS identity bootstrap

Use a dedicated sandbox with IAM Identity Center or short-lived role credentials. Never paste access keys in `.env` or into a prompt.

```bash
aws sso login --profile infra-agent-readonly
AWS_PROFILE=infra-agent-readonly aws sts get-caller-identity
```

This command verifies only which identity is active. It does not establish that the identity has an appropriate permission boundary; test that separately. The AWS MCP managed endpoint region and AWS **target resource** region are separate decisions. Follow the current official AWS setup guide and use SigV4 for headless/multi-account routing.

## First Codex assignment

> Read `AGENTS.md`, `docs/START_HERE.md`, `docs/PRODUCT_REQUIREMENTS.md`, `docs/SECURITY_AND_POLICY.md`, and `docs/IMPLEMENTATION_PLAN.md`. Implement Phase 0 and Phase 1 only. Use simulated AWS responses first. Do not implement or enable write-capable MCP credentials. Write tests for authorization denial, sanitized logs, account/region allowlisting, and read-only investigation. Show test output and explain limitations.

## Design rule to repeat throughout the build

**The planner may propose any documented operation. The executor may perform only those operations for which deterministic authorization and appropriately scoped AWS credentials exist.** Broad support is a product aspiration; it is not blanket permission.
