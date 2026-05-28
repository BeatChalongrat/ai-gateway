-- 018_enterprise_hardening.sql
--
-- Memory System S9 — Enterprise Hardening.
--
-- Three new tables + one optional column on chat.user_memories:
--
--   chat.tenant_memory_settings — per-tenant config knobs (memory
--     on/off, max memories per user, retention days, preference-
--     learning on/off, export + delete allowed). Missing row →
--     application reads sensible defaults; the table only carries
--     overrides.
--
--   chat.data_export_requests — audit trail for every export
--     generation. File lives on local storage at file_path until
--     expires_at (24h after generation by default).
--
--   chat.memory_usage_stats — placeholder for future periodic
--     aggregation. The admin /memory-stats endpoint queries live
--     data today; this table exists so a future aggregator job
--     can write daily snapshots without another migration.
--
--   chat.user_memories.embedding — optional vector(1536) column,
--     added ONLY when pgvector is installed (migration 010
--     enables it for the knowledge-base layer; if a deploy lands
--     without it, the DO block leaves the column absent and the
--     S9 search path falls back to the S6 tsvector).
--
-- Idempotent — safe to re-run.

-- ---------------------------------------------------------------------------
-- chat.tenant_memory_settings
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS chat.tenant_memory_settings (
    tenant_id UUID PRIMARY KEY,

    -- Master switch — when false, all memory features (save,
    -- inject, search, preference inference) silently no-op. Used
    -- when a tenant wants to disable the layer wholesale.
    memory_enabled BOOLEAN NOT NULL DEFAULT true,

    -- Hard cap on (active + pending) memories per user. CreateMemory
    -- refuses new rows when the user is at or above this number.
    max_memories_per_user INTEGER NOT NULL DEFAULT 200,

    -- NULL = no auto-expiry. Positive integer = soft-delete
    -- memories whose created_at is older than NOW() - interval.
    -- The RetentionEnforcer goroutine runs daily and skips pinned
    -- memories regardless of age.
    retention_days INTEGER,

    -- Can the S8 PreferenceExtractor run for this tenant?
    preference_learning_enabled BOOLEAN NOT NULL DEFAULT true,

    -- GDPR / privacy toggles. Both default true (open by default;
    -- enterprise deployments tighten where needed).
    allow_data_export   BOOLEAN NOT NULL DEFAULT true,
    allow_data_deletion BOOLEAN NOT NULL DEFAULT true,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- chat.memory_usage_stats — future aggregator target
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS chat.memory_usage_stats (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,

    period_start DATE NOT NULL,
    period_end   DATE NOT NULL,

    total_users_with_memories      INTEGER NOT NULL DEFAULT 0,
    total_active_memories          INTEGER NOT NULL DEFAULT 0,
    total_pending_memories         INTEGER NOT NULL DEFAULT 0,
    total_deleted_memories         INTEGER NOT NULL DEFAULT 0,
    total_memory_events            INTEGER NOT NULL DEFAULT 0,
    total_search_queries           INTEGER NOT NULL DEFAULT 0,
    total_preference_extractions   INTEGER NOT NULL DEFAULT 0,

    avg_memories_per_user NUMERIC(8,2) NOT NULL DEFAULT 0,
    top_memory_types JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (tenant_id, period_start)
);

CREATE INDEX IF NOT EXISTS idx_memory_usage_stats_tenant
    ON chat.memory_usage_stats(tenant_id, period_start DESC);

-- ---------------------------------------------------------------------------
-- chat.data_export_requests
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS chat.data_export_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    user_id   UUID NOT NULL,

    -- 'memories' (S9 ships only this; 'conversations' / 'full_account'
    -- documented for future expansion).
    export_type VARCHAR(30) NOT NULL,

    -- 'pending' | 'processing' | 'completed' | 'failed' | 'expired'
    status VARCHAR(20) NOT NULL DEFAULT 'pending',

    file_path       TEXT,
    file_size_bytes BIGINT,
    expires_at      TIMESTAMPTZ,

    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_data_export_user
    ON chat.data_export_requests(user_id, tenant_id);

-- ---------------------------------------------------------------------------
-- Optional: pgvector embedding column on chat.user_memories
-- ---------------------------------------------------------------------------
--
-- Wrapped in a DO block + pg_extension check so a deploy without
-- pgvector still applies cleanly — the embedding column + HNSW
-- index simply don't get created and the S6 tsvector path keeps
-- working. The chat-edge stack already enables pgvector for the
-- knowledge-base layer (migration 010), so in normal deploys this
-- branch fires.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
        ALTER TABLE chat.user_memories
            ADD COLUMN IF NOT EXISTS embedding vector(1536);

        -- HNSW index for fast cosine-similarity lookup. Conservative
        -- m / ef_construction values matching the chat-edge KB layer.
        CREATE INDEX IF NOT EXISTS idx_user_memories_embedding
            ON chat.user_memories
            USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64);

        RAISE NOTICE 'pgvector embedding column + HNSW index created on chat.user_memories';
    ELSE
        RAISE NOTICE 'pgvector not installed — skipping embedding column. Memory search will use tsvector only.';
    END IF;
END
$$;
