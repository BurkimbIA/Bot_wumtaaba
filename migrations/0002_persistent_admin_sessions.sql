CREATE TABLE IF NOT EXISTS wuumtaaba.admin_sessions (
    token_hash TEXT PRIMARY KEY, credential_hash TEXT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL, expires_at DOUBLE PRECISION NOT NULL);
CREATE INDEX IF NOT EXISTS admin_sessions_expiry ON wuumtaaba.admin_sessions(expires_at);
ALTER TABLE wuumtaaba.admin_sessions ENABLE ROW LEVEL SECURITY;
