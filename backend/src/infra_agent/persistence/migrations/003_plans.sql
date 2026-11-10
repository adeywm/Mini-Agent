-- Immutable plans, so approvers review exactly what will run.
CREATE TABLE IF NOT EXISTS plans (
    digest     TEXT PRIMARY KEY,
    task_id    TEXT        NOT NULL,
    payload    JSONB       NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS plans_task_idx ON plans (task_id);
