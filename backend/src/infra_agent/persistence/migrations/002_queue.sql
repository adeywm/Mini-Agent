-- Durable work queue: start/resume jobs with leases, bounded attempts and dead-lettering.
CREATE TABLE IF NOT EXISTS work_queue (
    id           BIGSERIAL PRIMARY KEY,
    task_id      TEXT        NOT NULL,
    kind         TEXT        NOT NULL CHECK (kind IN ('start', 'resume')),
    payload      JSONB       NOT NULL,
    dedupe_key   TEXT        UNIQUE,
    state        TEXT        NOT NULL DEFAULT 'pending'
                 CHECK (state IN ('pending', 'leased', 'done', 'dead')),
    attempts     INT         NOT NULL DEFAULT 0,
    max_attempts INT         NOT NULL DEFAULT 3,
    lease_owner  TEXT,
    lease_until  TIMESTAMPTZ,
    last_error   TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    available_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS work_queue_ready_idx ON work_queue (state, available_at);
