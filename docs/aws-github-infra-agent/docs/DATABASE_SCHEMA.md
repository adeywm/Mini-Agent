# Persistence and SQL Data Model

PostgreSQL is the source of truth for tasks, plans, approvals, actions, event deduplication and audit records. LangGraph checkpoint tables are separate and managed by the checkpointer library. Pin and test schema/migration versions; do not edit checkpointer tables by hand.

## Core entity relationships

`task 1:N plan`, `plan 1:N operation`, `plan 0:N approval`, `operation 0:N execution_attempt`, `task 1:N evidence`, `task 1:N audit_event`. Approved plan hash is immutable, not a mutable field on a pending proposal.

## Illustrative SQL DDL (adapt before production)

```sql
CREATE TABLE task (
  id UUID PRIMARY KEY,
  requester_id TEXT NOT NULL,
  account_alias TEXT NOT NULL,
  region TEXT NOT NULL,
  status TEXT NOT NULL,
  source TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE plan (
  id UUID PRIMARY KEY,
  task_id UUID NOT NULL REFERENCES task(id),
  plan_digest TEXT NOT NULL UNIQUE,
  canonical_plan JSONB NOT NULL,
  status TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE operation (
  id UUID PRIMARY KEY,
  plan_id UUID NOT NULL REFERENCES plan(id),
  idempotency_key TEXT NOT NULL UNIQUE,
  account_alias TEXT NOT NULL,
  region TEXT NOT NULL,
  service TEXT NOT NULL,
  api_operation TEXT NOT NULL,
  parameters_redacted JSONB NOT NULL,
  state TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE approval (
  id UUID PRIMARY KEY,
  plan_id UUID NOT NULL REFERENCES plan(id),
  plan_digest TEXT NOT NULL,
  approver_id TEXT,
  decision TEXT NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  decided_at TIMESTAMPTZ,
  CONSTRAINT valid_decision CHECK (decision IN ('PENDING','APPROVED','REJECTED','EXPIRED'))
);
CREATE TABLE execution_attempt (
  id UUID PRIMARY KEY,
  operation_id UUID NOT NULL REFERENCES operation(id),
  worker_id TEXT NOT NULL,
  state TEXT NOT NULL,
  aws_request_id TEXT,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ
);
CREATE TABLE inbound_event (
  provider TEXT NOT NULL,
  delivery_id TEXT NOT NULL,
  payload_hash TEXT NOT NULL,
  received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  task_id UUID,
  PRIMARY KEY (provider, delivery_id)
);
CREATE TABLE audit_event (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  task_id UUID NOT NULL REFERENCES task(id),
  actor_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  payload_redacted JSONB NOT NULL,
  occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

SQL illustrates logical constraints, not a completed production schema. Add tenant or organization ownership if supporting multiple users, row-level authorization as appropriate, indexes, version numbers, migration tool, immutable audit export and backups. Restrict DB write privileges: the agent planner must not directly update approvals or audit events.

## State machines

- Task: `NEW -> TRIAGING -> INVESTIGATING -> PLANNING -> POLICY_CHECK -> {AWAITING_APPROVAL, EXECUTING, DENIED} -> VERIFYING -> {COMPLETED, FAILED, NEEDS_REVIEW, CANCELLED}`.
- Approval: `PENDING -> {APPROVED, REJECTED, EXPIRED, CANCELLED}`. Terminal decisions cannot be edited.
- Operation: `PROPOSED -> AUTHORIZED -> IN_PROGRESS -> {SUCCEEDED, FAILED, UNKNOWN_OUTCOME, COMPENSATION_REQUIRED}`.

## Consistency and concurrency

Use transactional state transitions with compare-and-swap or row locking. The executor must first claim an operation under a unique idempotency key. External AWS calls cannot join a DB transaction; reconcile after timeouts/crashes. Do not retry an `UNKNOWN_OUTCOME` operation without checking external state. Audit records should be exported to an append-only, independently protected destination.

## Data retention and redaction

Prefer evidence references to raw logs. Store minimum data needed for diagnosis; encrypt sensitive metadata; redact headers, tokens, request bodies, credentials, secure parameter values, DB connection strings and customer records. Define log/task retention with operator and legal requirements.
