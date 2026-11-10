# Mini-Agent

A personal, chat-first AI assistant for **your GitHub and AWS accounts**. Ask in plain
language: "review the latest commit in my repo", "which EC2 instances are running?", "how big
are my S3 buckets?". The agent picks the right tools, shows every step it takes, and puts the
raw results next to the conversation.

It is built for one person: you connect your own accounts, and you are the only one who
approves anything risky.

> **Status (branch `v1`):** read-only. The agent can read GitHub and AWS; every create, update
> or delete is refused. Approval cards for changes are the next milestone.
> The earlier enterprise-style implementation is preserved on `main`, `beta` and `working`.

## What it does

- **Chat** with live, word-by-word answers and a Stop button.
- **GitHub** through the official [GitHub MCP Server](https://github.com/github/github-mcp-server):
  repositories, commits and diffs, issues, pull requests, releases, code search.
- **AWS** through any read-only AWS API call (EC2, S3, RDS, OpenSearch, ...) and the managed
  AWS MCP Server's documentation tools.
- **Tool transparency:** each answer shows "Used N tools"; expand it to see every call, its
  arguments, status and timing.
- **Results panel:** tool outputs stacked as numbered cards, shown as a sortable/filterable
  table, a commit diff, or JSON, with copy buttons.
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
   │      └─ Tool guard (code, not the model): decides each call's risk level
   │             L1 read → runs     L2/L3 change → refused (for now)     blocked → refused
   │
   ├─ GitHub MCP Server (remote)  ← token from `gh auth token`, never stored
   ├─ AWS: read-only boto3 calls  ← your AWS CLI profile, never stored
   │       + managed AWS MCP Server (docs tools)
   │
   └─ SQLite (database/mini-agent.db): chats, conversation state, connections
```

- **Model:** Claude Sonnet 4.5 on Bedrock (`global.anthropic.claude-sonnet-4-5-20250929-v1:0`), configurable.
- **Frameworks:** LangChain 1.x `create_agent` on LangGraph, `langchain-aws`,
  `langchain-mcp-adapters`, LangGraph SQLite checkpointer, FastAPI; React 18 + TypeScript + Vite.

## Safety model

- **Risk is decided in code** (`agents-server/app/risk.py`), before a tool runs:
  reads are L1, creates/updates L2, deletes L3. Anything unrecognised counts as a change.
- **Per-connection maximum level:** a connection capped at L1 can never change anything.
  A GitHub connection at L1 also asks the GitHub MCP Server for read-only tools only.
- **AWS reads are enforced twice:** the tool refuses non-read operations, and the boto3
  session itself blocks any non-read call before it leaves your machine.
- **Blocked tools:** `aws___run_script` (arbitrary code cannot be checked before it runs) and
  presigned URLs are never loaded.
- **Secrets are redacted** from tool output and tool arguments before the model or the UI
  sees them. The model gets at most 30,000 characters per result; the full result is kept
  only for the results panel.
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
uv run server.py            # MINI_AGENT_RELOAD=true to restart on code changes

# 2. Frontend (another terminal)
cd frontend
npm install
npm run dev                 # open the URL Vite prints, e.g. http://127.0.0.1:5173/
```

Then open **Connections** in the app and add:

| Type | Fields | Example |
|---|---|---|
| GitHub | name, `gh` user, maximum level | `personal`, `octocat`, L1 |
| AWS | name, AWS profile (empty = default), region, maximum level | `personal-aws`, `default`, `ap-south-1`, L1 |

### Settings (`agents-server/.env`)

| Variable | Default |
|---|---|
| `MINI_AGENT_MODEL_ID` | `global.anthropic.claude-sonnet-4-5-20250929-v1:0` |
| `MINI_AGENT_MODEL_REGION` | `ap-south-1` |
| `MINI_AGENT_BEDROCK_PROFILE` | default credential chain |
| `MINI_AGENT_DATABASE_PATH` | `../database/mini-agent.db` |
| `MINI_AGENT_AWS_MCP_ENDPOINT` / `_REGION` | `https://aws-mcp.us-east-1.api.aws/mcp` / `us-east-1` |
| `MINI_AGENT_GITHUB_MCP_URL` | `https://api.githubcopilot.com/mcp/` |
| `MINI_AGENT_RELOAD` | `false` |

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
  tests/                pytest (no network needed)
database/               local SQLite file (git-ignored)
frontend/               React + TypeScript chat UI
  src/components/       Sidebar, ChatView, ToolSteps, ResultsPanel, Composer, ...
backend/, config/, infra/, docs/   earlier implementation, kept for reference; to be removed from v1
```

## Development

```bash
cd agents-server && uv run pytest && uv run ruff check . && uv run ruff format --check .
cd frontend && npm run build        # type-check + production build
```

## Roadmap

1. **Approvals (next):** approval cards in the chat for L2/L3 actions (LangGraph `interrupt`),
   showing the exact action, resource and current → proposed values; protection against
   repeating a change whose outcome is unknown.
2. **GitHub work:** create and manage issues, create branches, commit changes and raise PRs
   through GitHub MCP, and read GitHub Actions results.
3. **AWS changes:** resize EC2/RDS, create resources, with cost and downtime notes on the card.
4. Remove the earlier implementation (`backend/`, `config/`, `infra/`, old docs) from `v1`.
