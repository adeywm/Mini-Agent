CREATE TABLE IF NOT EXISTS app_users (
    username TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    roles TEXT[] NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app_sessions (
    session_hash TEXT PRIMARY KEY,
    username TEXT NOT NULL REFERENCES app_users(username) ON DELETE CASCADE,
    csrf_token TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS app_sessions_expiry_idx ON app_sessions(expires_at);
