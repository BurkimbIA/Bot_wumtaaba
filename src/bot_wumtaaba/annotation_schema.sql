-- Durable annotation source, assignment, result and per-message history.
-- Audio bytes belong to the existing private Supabase Storage bucket.
CREATE TABLE IF NOT EXISTS annotation_tasks (
    task_id TEXT PRIMARY KEY, kind TEXT NOT NULL DEFAULT 'asr_preference', sample TEXT NOT NULL,
    audio_path TEXT NOT NULL, audio_sha256 TEXT NOT NULL, audio_mime TEXT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL, enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)));
CREATE TABLE IF NOT EXISTS annotation_assignments (
    recipient TEXT NOT NULL, task_id TEXT NOT NULL REFERENCES annotation_tasks(task_id),
    -- Historical link: outbox is purgeable and may be truncated by the legacy volume importer.
    -- Callback authorization rechecks the actual sent card instead of retaining outbox forever.
    source_outbox_id BIGINT NOT NULL, assigned_at DOUBLE PRECISION NOT NULL,
    status TEXT NOT NULL DEFAULT 'assigned' CHECK(status IN ('assigned','completed','cancelled')),
    PRIMARY KEY(recipient,task_id), UNIQUE(source_outbox_id));
CREATE TABLE IF NOT EXISTS annotation_events (
    source_message_id TEXT PRIMARY KEY, recipient TEXT NOT NULL, task_id TEXT NOT NULL,
    payload TEXT NOT NULL, created_at DOUBLE PRECISION NOT NULL,
    FOREIGN KEY(recipient,task_id) REFERENCES annotation_assignments(recipient,task_id));
CREATE TABLE IF NOT EXISTS annotation_responses (
    recipient TEXT NOT NULL, task_id TEXT NOT NULL,
    preference TEXT NOT NULL CHECK(preference IN ('A','B','tie','neither')),
    ratings TEXT NOT NULL, correction TEXT, sample TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('in_progress','completed')),
    source_message_id TEXT NOT NULL REFERENCES annotation_events(source_message_id),
    created_at DOUBLE PRECISION NOT NULL, updated_at DOUBLE PRECISION NOT NULL,
    PRIMARY KEY(recipient,task_id),
    FOREIGN KEY(recipient,task_id) REFERENCES annotation_assignments(recipient,task_id));
ALTER TABLE annotation_tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE annotation_assignments ENABLE ROW LEVEL SECURITY;
ALTER TABLE annotation_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE annotation_responses ENABLE ROW LEVEL SECURITY;
