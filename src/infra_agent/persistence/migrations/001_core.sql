-- Core task, evidence, approval, execution and audit records.
-- LangGraph checkpoints live in tables created by langgraph-checkpoint-postgres.

CREATE TABLE IF NOT EXISTS tasks (
    id            TEXT PRIMARY KEY,
    requester     TEXT        NOT NULL,
    origin        TEXT        NOT NULL,
    intent        TEXT        NOT NULL,
    account_alias TEXT        NOT NULL,
    region        TEXT        NOT NULL,
    status        TEXT        NOT NULL,
    payload       JSONB       NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL,
    last_updated  TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS tasks_status_idx ON tasks (status);

CREATE TABLE IF NOT EXISTS evidence (
    task_id      TEXT        NOT NULL REFERENCES tasks (id),
    id           TEXT        NOT NULL,
    source       TEXT        NOT NULL,
    digest       TEXT        NOT NULL,
    collected_at TIMESTAMPTZ NOT NULL,
    payload      JSONB       NOT NULL,
    PRIMARY KEY (task_id, id)
);

CREATE TABLE IF NOT EXISTS approvals (
    id          TEXT PRIMARY KEY,
    task_id     TEXT        NOT NULL REFERENCES tasks (id),
    plan_digest TEXT        NOT NULL,
    decision    TEXT        NOT NULL,
    requester   TEXT        NOT NULL,
    approver    TEXT,
    expires_at  TIMESTAMPTZ NOT NULL,
    used_at     TIMESTAMPTZ,
    payload     JSONB       NOT NULL
);

CREATE TABLE IF NOT EXISTS execution_records (
    id                    TEXT PRIMARY KEY,
    task_id               TEXT        NOT NULL,
    operation_id          TEXT        NOT NULL,
    plan_digest           TEXT        NOT NULL,
    operation_fingerprint TEXT        NOT NULL,
    status                TEXT        NOT NULL,
    recorded_at           TIMESTAMPTZ NOT NULL,
    payload               JSONB       NOT NULL
);
-- One successful execution per operation fingerprint (idempotency).
CREATE UNIQUE INDEX IF NOT EXISTS execution_once_idx
    ON execution_records (operation_fingerprint) WHERE status = 'SUCCEEDED';

CREATE TABLE IF NOT EXISTS audit_events (
    seq            BIGSERIAL PRIMARY KEY,
    id             TEXT        NOT NULL UNIQUE,
    correlation_id TEXT        NOT NULL,
    actor          TEXT        NOT NULL,
    action         TEXT        NOT NULL,
    outcome        TEXT        NOT NULL,
    occurred_at    TIMESTAMPTZ NOT NULL,
    payload        JSONB       NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_correlation_idx ON audit_events (correlation_id);

-- Audit is append-only: reject UPDATE and DELETE at the database level.
CREATE OR REPLACE FUNCTION audit_events_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_events is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS audit_events_no_update ON audit_events;
CREATE TRIGGER audit_events_no_update
    BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION audit_events_immutable();

CREATE TABLE IF NOT EXISTS event_inbox (
    dedupe_key  TEXT PRIMARY KEY,
    provider    TEXT        NOT NULL,
    task_id     TEXT,
    received_at TIMESTAMPTZ NOT NULL,
    payload     JSONB       NOT NULL
);
