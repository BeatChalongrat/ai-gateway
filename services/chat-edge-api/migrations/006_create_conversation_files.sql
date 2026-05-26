-- 006_create_conversation_files.sql
--
-- File attachments live alongside the chat domain in their own
-- table. ``conversation_files`` is the canonical row; the blob
-- itself lives on disk under ``./data/uploads/{tenant_id}/...``
-- with ``storage_path`` pointing at it.
--
-- ``message_id`` is nullable: a file is uploaded BEFORE the user
-- presses send, so it spends a few seconds detached from any
-- message. On send the message handler patches in the message id.
--
-- ``extracted_text`` stores the AI-ready content (text, csv body,
-- or an "[Image …]" placeholder for binary types we can't read
-- inline). Truncated to 50k characters by the extractor; the
-- column itself is unbounded TEXT.

CREATE TABLE IF NOT EXISTS chat.conversation_files (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id     UUID NOT NULL REFERENCES chat.conversations(id) ON DELETE CASCADE,
    -- Points at chat.messages_masked, not chat.messages — that
    -- table name lives on disk to keep the privacy migration
    -- path forward-compatible.
    message_id          UUID REFERENCES chat.messages_masked(id),
    tenant_id           UUID NOT NULL,
    filename            VARCHAR(255) NOT NULL,           -- sanitized, used in storage path
    original_filename   VARCHAR(255) NOT NULL,           -- what the user uploaded
    content_type        VARCHAR(100) NOT NULL,
    file_size_bytes     BIGINT NOT NULL,
    storage_path        VARCHAR(500) NOT NULL,
    extracted_text      TEXT,
    extraction_status   VARCHAR(20) NOT NULL DEFAULT 'pending'
                        CHECK (extraction_status IN ('pending', 'processing', 'completed', 'failed', 'unsupported')),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conv_files_conversation ON chat.conversation_files(conversation_id);
CREATE INDEX IF NOT EXISTS idx_conv_files_tenant       ON chat.conversation_files(tenant_id);
CREATE INDEX IF NOT EXISTS idx_conv_files_message      ON chat.conversation_files(message_id);
