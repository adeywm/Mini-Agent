# Decisions, Assumptions, and References

## ADR-001 — Choose LangGraph for orchestration

**Decision:** LangGraph + LangChain in Python as orchestration/runtime client, with AWS Bedrock model provider. Reason: explicit state graph, persistent checkpoint/resume, HITL via interrupts, flexible tool adapters. This is a design choice, not a claim Strands cannot implement the same product.

**Alternative:** Strands Agents is viable, particularly for rapid AWS-focused prototypes. Do not combine both orchestration frameworks in the initial implementation.

## ADR-002 — Use managed AWS MCP for discovery, not privileged mutation

**Decision:** Managed AWS MCP Server for broad AWS documentation + discovery; official GitHub MCP for repo read operations. Broad privileged AWS writes use a separate structured broker with Boto3 and policy enforcement.

**Reason:** `aws___run_script` can invoke multiple arbitrary Boto3 calls; generic script inspection is not a reliable authorization mechanism. IAM limits on read-only MCP connection and a separate credential path prevent bypass. MCP client-side settings and model instructions are not adequate boundaries.

## ADR-003 — Treat broad AWS support as extensibility objective

**Decision:** Dynamic SDK operation discovery and shape validation instead of thousands of hard-coded LangChain tools. Unknown operations are disabled for writes until risk and policy controls exist. Specialized workflows handle complex operations.

## ADR-004 — Risk-based HITL

**Decision:** Low-risk and explicitly authorized changes can be unattended. Higher-risk operations require approval tied to exact plan, TTL and intended scope. Hard-denied actions cannot be overridden by ordinary approvals.

## ADR-005 — GitHub write isolation

**Decision:** GitHub App with repository-scoped tokens; protected PR workflow and CI for IaC. GitHub MCP read-only for discovery; a controlled GitHub write broker for mutation.

## ADR-006 — Single operator first

**Decision:** Initial environment is one authorized operator and their configured accounts. Multi-tenant credential isolation and delegated approval policy are future work, not implied by this initial spec.

## ADR-007 — LangChain agents for reasoning, LangGraph for orchestration

**Decision:** LangChain `create_agent` (itself built on LangGraph) for open-ended reasoning and tool selection; explicit LangGraph graphs for deterministic workflows, durable state, `interrupt()`-based approval and recovery. Agent interfaces stay callable from a LangGraph supervisor. The permission engine, policy engine and execution broker are plain Python modules so they survive a framework change.

## ADR-008 — Three permission levels + separate risk policy

**Decision:** L1 READ / L2 CREATE-UPDATE / L3 DELETE gate what may be requested; a separate risk policy decides auto vs approval vs deny; IAM and GitHub App permissions enforce independently. See `SECURITY_AND_POLICY.md`.

## ADR-009 — Generic AWS discovery from botocore models

**Decision:** Discover and validate any AWS operation from the botocore service models shipped with the SDK; classify access deterministically; no per-service tools. Service-specific logic lives only in optional playbooks (e.g. `playbooks/ecs_health`).

## Important implementation assumptions

- The operator will supply explicit sandbox accounts, resource tags, GitHub repo allowlists, budget, approver identity and permitted API classes.
- The docs do not provision AWS resources and do not demonstrate any live execution.
- AWS endpoint/tool names, GitHub MCP capabilities and AgentCore deployment interfaces can change; inspect upstream docs in the pinned release/version at implementation time.
- Cost estimates can be approximate or unavailable; never bypass risk review because an estimate is missing.

## Verified official references (checked November 2026)

- AWS MCP setup, auth methods, endpoints and region list: https://docs.aws.amazon.com/agent-toolkit/latest/userguide/getting-started-aws-mcp-server.html
- AWS MCP tool names and capabilities: https://docs.aws.amazon.com/agent-toolkit/latest/userguide/understanding-mcp-server-tools.html
- AWS MCP proxy: https://github.com/aws/mcp-proxy-for-aws
- GitHub MCP Server: https://github.com/github/github-mcp-server
- GitHub MCP toolsets and read-only: https://github.com/github/github-mcp-server/blob/main/docs/server-configuration.md
- LangGraph interrupts / checkpointing: https://docs.langchain.com/oss/python/langgraph/interrupts
- LangChain MCP adapters: https://github.com/langchain-ai/langchain-mcp-adapters
- Bedrock AgentCore capabilities: https://aws.amazon.com/bedrock/agentcore/faqs/
- Bedrock AgentCore starter toolkit: https://github.com/aws/bedrock-agentcore-starter-toolkit

## To confirm before implementation

- Exact SDK and managed MCP versions, MCP proxy Python integration and supported Python versions.
- Required SigV4 permissions and target service-specific IAM condition keys.
- Supported target-region selection inside the managed MCP `aws___run_script` tool.
- Production LangGraph checkpointer/database compatibility with the selected runtime.
- GitHub App minimum permissions and OIDC claims for chosen repositories.
- AgentCore deployment/invocation/authentication details for the selected hosting pattern.
