# Mini-Agent

A personal, chat-first AI assistant for **your GitHub and AWS accounts**. Ask in plain
language: "review the latest commit in my repo", "which EC2 instances are running?", "how big
are my S3 buckets?". The agent picks the right tools, shows every step it takes, and puts the
raw results next to the conversation.

It is built for one person: you connect your own accounts, and you are the only one who
approves anything risky.

> **Status (branch `v1`):** reads run automatically; creates, updates, deletes and IAM calls
> wait for your approval in the chat. The approval flow is tested with mocked tools only; try
> your first real change on a sandbox resource.

## What it does

- **Chat** with live, word-by-word answers and a Stop button.
- **GitHub** through the official [GitHub MCP Server](https://github.com/github/github-mcp-server):
  repositories, commits and diffs, issues, pull requests, releases, code search.
- **AWS** through any read-only AWS API call (EC2, S3, RDS, OpenSearch, ...), the managed
  AWS MCP Server's documentation tools, `aws_describe_operation` for exact parameters, and one API call
  at a time for changes: `aws_write` (L2 create/update) and `aws_critical` (L3 delete/terminate
  and every IAM call).
- **Approvals:** every change pauses as a card in the chat: connection and account, the exact
  tool call and arguments, its level (L2/L3) and reason, and cost, downtime or
  irreversibility warnings. Approve runs exactly that call; Reject tells the agent not to.
- **Tool transparency:** tool steps are visible in each answer. Open a step to inspect its
  exact tool name, arguments, status and returned output. Sensitive values may be redacted.
- **Results panel:** tool outputs stacked as numbered cards, shown as a sortable/filterable
  table, a commit diff, or JSON, with copy buttons.
- **Settings:** one dialog (sidebar, bottom) with Connections, Model and Appearance tabs.
- **Appearance:** light or AMOLED dark mode under Settings; the choice is saved locally.
- **Connections:** the Connections dialog checks each account live (Signed in, Sign-in
  needed, Error). A signed-out account gets a **Sign in** button: AWS runs `aws sso login`
  or `aws login` and opens the browser; GitHub runs `gh auth login --web` and shows the
  one-time code. The dialog waits until the account is signed in. Setup the dialog cannot
  answer (missing AWS profile, no `gh`) shows the command to run in a terminal.
- **Connection errors:** expired AWS CLI and SSO sessions in a chat show a sign-in prompt with
  the affected connection or Bedrock profile. AWS service errors show the operation and error code.
- **Bedrock model:** choose a model or inference profile and source region under Settings > Model.
  Load the catalog on demand or enter an ID manually. India geographic profiles (`in.`) are
  grouped separately; global profiles can route outside India. The selection is saved in SQLite
  and applies to subsequent messages.
- **Chats** are saved locally, grouped by date, searchable and renameable.

## How it works

```text
Browser (React chat UI)
   │  /api  (localhost only)
   ▼
agents-server (FastAPI)
   │
   ├─ LangChain agent (create_agent) ── Claude on Amazon Bedrock
   │      │
   │      ├─ Tool guard (code, not the model): risk level; blocked or unknown → refused
   │      ├─ LangChain HumanInTheLoopMiddleware (per tool call)
   │      │      L1 read → runs     L2 create/update, L3 delete/IAM → approval card
   │      │      above the connection's default level, the card also elevates it
   │      │      for this request only
   │      └─ Execution ledger (SQLite): an approved change runs at most once
   │
   ├─ GitHub MCP Server (remote)  ← token from `gh auth token`, never stored
   ├─ AWS: read-only boto3 calls  ← your AWS CLI profile, never stored
   │       + managed AWS MCP Server (docs tools)
   │
   └─ SQLite (database/mini-agent.db): chats, conversation state and paused approvals
      (LangGraph checkpointer), connections, approved-change records
```

- **Model:** Claude Sonnet 4.5 on Bedrock (`global.anthropic.claude-sonnet-4-5-20250929-v1:0`), configurable.
- **Frameworks:** LangChain 1.x `create_agent` on LangGraph, `langchain-aws`,
  `langchain-mcp-adapters`, LangGraph SQLite checkpointer, FastAPI; React 18 + TypeScript + Vite.

## Safety model

- **Risk is decided in code** (`agents-server/app/risk.py`), before a tool runs:
  list/get/describe/search are L1, create/update L2, delete/terminate and every AWS IAM call
  L3. Anything unrecognised counts as a change.
- **L1 runs automatically; L2 and L3 always ask you** (LangChain `HumanInTheLoopMiddleware`,
  one interrupt per tool call, approve or reject only, no edits). The paused run is saved in
  SQLite, so a pending approval survives a server restart.
- **Default level per connection, elevated per request:** a connection's level is its default
  (usually L1). A change above it does not need a settings change: its approval card also
  elevates that connection for the current request only ("Elevate to L3 & approve"). The next
  message starts at the default level again. Every L2/L3 change still gets its own card.
- **Approval binds to the exact call:** the card carries a digest of the tool name and
  arguments; a different call cannot be approved with it. Approve is disabled when any
  argument is hidden as a secret.
- **No duplicate changes:** each approved change is recorded before it runs and never runs
  twice. A change cut off by a crash or Stop is reported as "outcome unknown", not retried.
  Sending a new message instead of answering a card leaves that change not run.
- **Approval grants nothing extra:** a change runs with your connection's own `gh` login or
  AWS profile, which needs the underlying GitHub or IAM permissions.
- **`aws_read` only reads:** the tool refuses non-read operations, and its boto3 session
  blocks any non-read call before it leaves your machine. Changes go through `aws_write`
  or `aws_critical`: one explicit service/operation/parameters call, classified in code (the
  wrong tool for an operation's level is refused), checked against the AWS API model before the
  card, never auto-retried.
- **Blocked always:** secret reads (e.g. `GetSecretValue`, `GetParameter`, credential
  reports), `aws___run_script` (arbitrary code cannot be checked before it runs), presigned
  URLs, and shell operators in AWS CLI commands.
- **Sensitive values are redacted** from tool output and arguments using pattern-based checks.
  Redaction is not guaranteed to catch every secret. The model gets at most 30,000 characters
  per result; the results panel keeps up to 2,000,000 characters of the redacted output.
- **Stop** never claims a step was undone: a step cut off by Stop is shown as
  "outcome unknown".
- **Local only:** the server binds to `127.0.0.1`, rejects other host names, and requires a
  custom header on every change request (no cross-site requests).
- **No stored credentials:** a connection stores only a `gh` user name or an AWS profile name.

## Requirements

- [uv](https://docs.astral.sh/uv/) (Python 3.12 is installed by uv) and Node.js 20+.
- [GitHub CLI](https://cli.github.com/) signed in: `gh auth login`.
- AWS CLI with a working profile (`aws login` or `aws sso login`) and access to a Claude
  model in Amazon Bedrock.

## Run it

```bash
# 1. Backend (http://127.0.0.1:8000)
cd agents-server
cp .env.example .env        # optional: model id, region, Bedrock profile
uv sync
uv run server.py            # restarts on code changes; MINI_AGENT_RELOAD=false to turn off

# 2. Frontend (another terminal)
cd frontend
npm install
npm run dev                 # open the URL Vite prints, e.g. http://127.0.0.1:5173/
```

Open **Settings > Connections** (sidebar, bottom) to add an account. GitHub uses a `gh auth login` session; AWS uses an AWS CLI profile. The dialog suggests the `gh` accounts and AWS profiles found on this machine, and offers **Sign in** when a session is missing or expired. **Disconnect** removes that account configuration from Mini-Agent; it does not revoke the underlying login or profile.

Choose:

| Type | Fields | Example |
|---|---|---|
| GitHub | name, `gh` user, default level | `personal`, `octocat`, L1 |
| AWS | name, AWS profile (empty = default), region, default level | `personal-aws`, `default`, `ap-south-1`, L1 |

### Settings (`agents-server/.env`)

| Variable | Default |
|---|---|
| `MINI_AGENT_MODEL_ID` | `global.anthropic.claude-sonnet-4-5-20250929-v1:0` |
| `MINI_AGENT_MODEL_REGION` | `ap-south-1` |
| `MINI_AGENT_BEDROCK_PROFILE` | default credential chain |
| `MINI_AGENT_DATABASE_PATH` | `../database/mini-agent.db` |
| `MINI_AGENT_AWS_MCP_ENDPOINT` / `_REGION` | `https://aws-mcp.us-east-1.api.aws/mcp` / `us-east-1` |
| `MINI_AGENT_GITHUB_MCP_URL` | `https://api.githubcopilot.com/mcp/` |
| `MINI_AGENT_RELOAD` | `true` |

The model ID and region above are defaults. Choosing a model in the app saves a local preference
that takes precedence for later messages. Catalog discovery uses the configured Bedrock profile
and the chosen region. Listing entries requires the corresponding Bedrock listing permissions;
it does not guarantee permission, streaming support, or access to invoke every listed profile.

## Project layout

```text
agents-server/          Python backend (uv project)
  server.py             start the API on localhost
  app/api.py            HTTP API: chats, connections, streamed replies
  app/agent.py          agent, per-connection toolset, tool guard, redaction
  app/risk.py           L1/L2/L3 risk of every GitHub/AWS tool call
  app/tools/            GitHub MCP, AWS read tools + AWS MCP
  app/security/         AWS access classification + read-only guard, redaction
  app/db.py             SQLite store for chats and connections
  app/accounts.py       connection sign-in checks (gh, AWS STS) and CLI sign-in
  tests/                pytest (no network needed)
database/               local SQLite file (git-ignored)
frontend/               React + TypeScript chat UI
  src/components/       Sidebar, ChatView, ToolSteps, ResultsPanel, Composer, ...
```

## Development

```bash
cd agents-server && uv run pytest && uv run ruff check . && uv run ruff format --check .
cd frontend && npm test && npm run build  # tool status checks, type-check and build
```

## Roadmap

1. **Approvals (done):** approval cards for L2/L3 changes with LangChain human-in-the-loop.
2. **GitHub work:** create and manage issues, create branches, commit changes and raise PRs
   through GitHub MCP, and read GitHub Actions results.
3. **AWS changes:** resize EC2/RDS, create resources, with cost and downtime notes on the card.

## Future scope

Planned order: multiple MCP servers, then the knowledge base, then learning from feedback.
Everything stays local, in line with the single-user design.

1. **Multiple MCP servers.** Add any MCP server (local command or HTTP URL) as a connection
   next to GitHub and AWS. Unknown tools count as changes (L2) and need an approval card until
   the user marks a tool read-only (L1). Tool descriptions from a server are untrusted text,
   never instructions.
2. **Personal knowledge base.** Index a local folder of notes, docs and runbooks with Bedrock
   embeddings and a vector index in SQLite, exposed to the agent as a "search my notes" tool.
   Retrieved text is data, never instructions. Bedrock Knowledge Bases is the cloud
   alternative (needs S3 and OpenSearch).
3. **Learning from feedback.** Not RLHF: the Bedrock model's weights cannot be retrained.
   Instead: thumbs up/down and an optional note on each answer; corrections saved as
   preferences that go into the system prompt; good answers reused as examples for similar
   questions. A settings page shows what was learned and lets the user delete it.
4. **Bedrock AgentCore (optional).** Only if Mini-Agent must serve several users or run
   without the user's machine: GitHub through AgentCore Identity (OAuth), AWS through an
   execution role and assume-role, state in DynamoDB or RDS, Cognito sign-in, and the UI
   hosted on S3 + CloudFront.
