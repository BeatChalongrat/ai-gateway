-- 049_kb_local_hybrid.sql — FIX132 B: the knowledge base on local models.
--
-- The knowledge base now embeds with bge-m3 in kb-models on this machine
-- (1024-d dense + sparse lexical weights) and also indexes each passage for
-- Postgres full-text search over PyThaiNLP-segmented text. Documents are
-- indexed in the background with visible progress, and every knowledge base
-- belongs to the user who created it.
--
-- Idempotent: every step can run again on a database that already has it.
-- The data lane's migrations start at 050.

-- ── knowledge_documents: background indexing + progress ──────────────────
ALTER TABLE chat.knowledge_documents ADD COLUMN IF NOT EXISTS progress_done   INT DEFAULT 0;
ALTER TABLE chat.knowledge_documents ADD COLUMN IF NOT EXISTS progress_total  INT DEFAULT 0;
-- unsupported_type | too_large | empty_text | encrypted | extract_failed |
-- models_unavailable | internal (error_message keeps the human sentence).
ALTER TABLE chat.knowledge_documents ADD COLUMN IF NOT EXISTS error_code      VARCHAR(40);
ALTER TABLE chat.knowledge_documents ADD COLUMN IF NOT EXISTS uploaded_by     UUID;
ALTER TABLE chat.knowledge_documents ADD COLUMN IF NOT EXISTS indexed_at      TIMESTAMPTZ;
-- kb-models unreachable → retried with backoff; failed after the retries.
ALTER TABLE chat.knowledge_documents ADD COLUMN IF NOT EXISTS attempts        INT DEFAULT 0;
ALTER TABLE chat.knowledge_documents ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMPTZ;
-- Set when a worker claims the document (FOR UPDATE SKIP LOCKED) and renewed
-- on every batch; a 'processing' row whose claim went stale (a restart) goes
-- back to 'pending'.
ALTER TABLE chat.knowledge_documents ADD COLUMN IF NOT EXISTS claimed_at      TIMESTAMPTZ;

-- ── knowledge_chunks: bge-m3 (1024-d) + sparse + full-text ───────────────
-- The ivfflat index was built on an empty table (pgvector advises against
-- that: its lists are trained on the rows present at build time). Retrieval
-- is an exact scan per knowledge base (btree idx_kc_kb); at pilot scale that
-- is a few milliseconds. Add HNSW (with iterative scan for the knowledge-base
-- filter) when one knowledge base passes ~50,000 chunks or the dense arm
-- exceeds ~100 ms — docs/ops/knowledge-base.md.
DROP INDEX IF EXISTS chat.idx_kc_embedding;

-- Vectors from the old 1536-d model cannot be searched with bge-m3 queries:
-- delete them and send their documents back to be indexed again.
WITH stale AS (
    DELETE FROM chat.knowledge_chunks
    WHERE embedding IS NOT NULL AND vector_dims(embedding) <> 1024
    RETURNING document_id
)
UPDATE chat.knowledge_documents d
SET status = 'pending', chunk_count = 0, error_message = NULL, error_code = NULL,
    progress_done = 0, progress_total = 0, attempts = 0, next_attempt_at = NULL,
    claimed_at = NULL, indexed_at = NULL, updated_at = NOW()
WHERE d.id IN (SELECT DISTINCT document_id FROM stale);

DO $$
BEGIN
    IF (SELECT format_type(a.atttypid, a.atttypmod)
        FROM pg_attribute a
        WHERE a.attrelid = 'chat.knowledge_chunks'::regclass
          AND a.attname = 'embedding' AND NOT a.attisdropped) <> 'vector(1024)' THEN
        ALTER TABLE chat.knowledge_chunks ALTER COLUMN embedding TYPE vector(1024);
    END IF;
END $$;

-- bge-m3's lexical weights over its 250,002-token vocabulary. pgvector's
-- sparsevec literal is 1-based: {token_id+1:weight,…}/250002. Scored with
-- <#> (negative inner product) = Σ q_w·d_w over shared tokens.
ALTER TABLE chat.knowledge_chunks ADD COLUMN IF NOT EXISTS sparse      sparsevec(250002);
-- to_tsvector('simple', <text segmented into words by kb-models /v1/segment>)
-- — Thai has no spaces between words, so the text is segmented first.
ALTER TABLE chat.knowledge_chunks ADD COLUMN IF NOT EXISTS content_tsv tsvector;
CREATE INDEX IF NOT EXISTS idx_kc_content_tsv ON chat.knowledge_chunks USING GIN (content_tsv);

-- ── knowledge_bases: an owner; the new default model ─────────────────────
-- A knowledge base is private to the user who created it (like a personal
-- project in ChatGPT / Claude). Rows from before this migration have no
-- owner and are reachable only through the admin endpoints.
ALTER TABLE chat.knowledge_bases ADD COLUMN IF NOT EXISTS user_id UUID;
ALTER TABLE chat.knowledge_bases ALTER COLUMN embedding_model SET DEFAULT 'bge-m3';
UPDATE chat.knowledge_bases SET embedding_model = 'bge-m3'
WHERE embedding_model IS DISTINCT FROM 'bge-m3';
