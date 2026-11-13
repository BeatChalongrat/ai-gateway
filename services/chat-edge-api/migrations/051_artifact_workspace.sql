-- Durable, owner-scoped drafts and review proposals. Pending candidates are
-- not conversation files and cannot appear in chat/model context before accept.
CREATE TABLE IF NOT EXISTS chat.file_drafts (
    file_id uuid PRIMARY KEY REFERENCES chat.conversation_files(id) ON DELETE CASCADE,
    tenant_id uuid NOT NULL,
    user_id uuid NOT NULL,
    source_sha text NOT NULL,
    revision bigint NOT NULL DEFAULT 1,
    body jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (octet_length(body::text) <= 2097152)
);
CREATE TABLE IF NOT EXISTS chat.file_work (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    file_id uuid NOT NULL REFERENCES chat.conversation_files(id) ON DELETE CASCADE,
    tenant_id uuid NOT NULL,
    user_id uuid NOT NULL,
    request jsonb NOT NULL,
    status text NOT NULL DEFAULT 'running',
    stage text NOT NULL DEFAULT 'inspect',
    checkpoint jsonb NOT NULL DEFAULT '{}',
    candidate bytea,
    error text NOT NULL DEFAULT '',
    result_file_id uuid REFERENCES chat.conversation_files(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL DEFAULT now() + interval '1 day',
    CHECK (status IN ('running','ready','unchanged','failed','cancelled','accepting','accepted','rejected')),
    CHECK (octet_length(checkpoint::text) <= 33554432),
    CHECK (octet_length(candidate) <= 16777216)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_file_work_active ON chat.file_work(file_id) WHERE status IN ('running','ready','accepting');
CREATE INDEX IF NOT EXISTS idx_file_work_owner ON chat.file_work(tenant_id,user_id,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_file_drafts_owner ON chat.file_drafts(tenant_id,user_id,updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_file_library_owner ON chat.conversation_files(tenant_id,user_id,created_at DESC);
