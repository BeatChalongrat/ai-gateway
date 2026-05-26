-- 011_create_knowledge_base.sql
--
-- Adds the RAG / knowledge-base storage layer:
--
--   chat.knowledge_bases    one "library" per tenant; users can have
--                           several (e.g. "Company FAQ", "Onboarding").
--   chat.knowledge_documents one row per uploaded file; status moves
--                           pending → processing → indexed | failed.
--   chat.knowledge_chunks    the embedded slices; pgvector(1536)
--                           column carries the embedding.
--
-- conversations.knowledge_base_id is the per-conversation binding;
-- nullable because most conversations have no KB attached.

CREATE TABLE IF NOT EXISTS chat.knowledge_bases (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL,
    name            VARCHAR(255) NOT NULL,
    description     TEXT DEFAULT '',
    embedding_model VARCHAR(100) DEFAULT 'text-embedding-3-small',
    chunk_size      INT  DEFAULT 512,
    chunk_overlap   INT  DEFAULT 50,
    is_active       BOOLEAN DEFAULT TRUE,
    document_count  INT  DEFAULT 0,
    total_chunks    INT  DEFAULT 0,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_kb_tenant ON chat.knowledge_bases(tenant_id);

CREATE TABLE IF NOT EXISTS chat.knowledge_documents (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    knowledge_base_id  UUID NOT NULL REFERENCES chat.knowledge_bases(id) ON DELETE CASCADE,
    tenant_id          UUID NOT NULL,
    filename           VARCHAR(255) NOT NULL,
    original_filename  VARCHAR(255) NOT NULL,
    content_type       VARCHAR(100),
    file_size_bytes    BIGINT DEFAULT 0,
    storage_path       VARCHAR(500),
    status             VARCHAR(20) DEFAULT 'pending'
        CHECK (status IN ('pending','processing','indexed','failed')),
    chunk_count        INT DEFAULT 0,
    char_count         INT DEFAULT 0,
    error_message      TEXT,
    created_at         TIMESTAMPTZ DEFAULT NOW(),
    updated_at         TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_kd_kb     ON chat.knowledge_documents(knowledge_base_id);
CREATE INDEX IF NOT EXISTS idx_kd_tenant ON chat.knowledge_documents(tenant_id);

CREATE TABLE IF NOT EXISTS chat.knowledge_chunks (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id        UUID NOT NULL REFERENCES chat.knowledge_documents(id) ON DELETE CASCADE,
    knowledge_base_id  UUID NOT NULL,
    tenant_id          UUID NOT NULL,
    content            TEXT NOT NULL,
    embedding          vector(1536),
    chunk_index        INT DEFAULT 0,
    metadata           JSONB DEFAULT '{}',
    created_at         TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_kc_kb        ON chat.knowledge_chunks(knowledge_base_id);
CREATE INDEX IF NOT EXISTS idx_kc_doc       ON chat.knowledge_chunks(document_id);
-- IVFFLAT cosine index. ``lists=100`` is the recommended starting
-- point for tables in the 10k–1M row range; we can rebuild with a
-- different list count once production usage shapes the distribution.
CREATE INDEX IF NOT EXISTS idx_kc_embedding ON chat.knowledge_chunks
    USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- Conversation → KB binding. Nullable; conversations default to "no
-- KB". ``ON DELETE SET NULL`` is the right cascade — deleting a KB
-- shouldn't kill the conversations that referenced it.
ALTER TABLE chat.conversations
    ADD COLUMN IF NOT EXISTS knowledge_base_id UUID
    REFERENCES chat.knowledge_bases(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_conversations_kb
    ON chat.conversations(knowledge_base_id);
