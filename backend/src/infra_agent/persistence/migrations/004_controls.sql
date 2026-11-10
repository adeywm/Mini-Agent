-- Operator controls (kill switch). Brokers read this before every write.
CREATE TABLE IF NOT EXISTS controls (
    key        TEXT PRIMARY KEY,
    value      JSONB       NOT NULL,
    updated_by TEXT        NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
