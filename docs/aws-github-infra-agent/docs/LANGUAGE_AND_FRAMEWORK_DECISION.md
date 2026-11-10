# Architecture Decision: Python + LangGraph, while retaining TypeScript and Strands as valid alternatives

**Chosen initial implementation:** Python 3.12+, `uv`, LangGraph/LangChain, AWS SDK for Python (Boto3/botocore), Bedrock, and FastAPI. Optional UI: TypeScript/React.

## Why this decision

Both Strands Agents and LangChain/LangGraph have Python and TypeScript support. Likewise, the AWS SDK for JavaScript v3 supports AWS API operations. This is not a language-availability decision.

Choose Python because (1) botocore service models provide a convenient foundation for dynamic API request validation; (2) Boto3 is widely used for infrastructure automation; (3) one runtime can host LangGraph workflows, Bedrock integrations, FastAPI, and a tightly controlled broker; and (4) it avoids a second language in the MVP service backend. This is a maintainability trade-off, not proof Python is universally faster or safer than TypeScript.

## Alternatives

| Option | Benefits | Trade-offs for this project |
|---|---|---|
| Python + LangGraph (selected) | Explicit checkpoints/HITL, Boto3 model access, unified backend | Need to design privileged execution boundary yourself |
| Python + Strands | AWS-centered developer experience, concise tooling | Similar security boundary still required |
| TypeScript + LangGraph.js | Single JS/TS stack with frontend, strong typing | Implement AWS SDK operation catalog and policy adapters in TypeScript |
| TypeScript + Strands | JS/TS integration and agent features | Same requirement for external deterministic authorization and durable workflow semantics |

Whichever framework is chosen, **agent tool definitions are not security controls**. IAM, policy engine, credentials and audit design remain mandatory.

## Reconsider this decision if

- The main project team is substantially more proficient in TypeScript.
- Existing service infrastructure uses NestJS/Node, a shared typed SDK and policy control plane.
- A measurable prototype demonstrates better total cost, testability or operational characteristics with Strands or LangGraph.js.

Don't mix two orchestration frameworks within the MVP unless a tested integration requirement cannot otherwise be met.
