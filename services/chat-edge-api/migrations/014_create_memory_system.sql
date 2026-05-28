-- 014_create_memory_system.sql
--
-- Memory System S1 — Personal Memory Core.
--
-- Two tables + one toggle column:
--
--   chat.user_memories — the durable facts/preferences/instructions
--     the AI remembers about the user across conversations. Keyed
--     primarily by (user_id, tenant_id); scope_type lets a memory
--     also pin to a single conversation or artifact.
--
--   chat.memory_events — append-only audit trail for every memory
--     mutation (created / updated / deleted / pinned / confirmed /
--     recalled). Read by the audit endpoint and by future support
--     workflows that need to explain why the AI "remembers" a thing.
--
--   chat.conversations.memory_enabled — per-conversation toggle for
--     the upcoming "temporary chat" mode. When false, neither the
--     memory-context system-prompt block nor the save_memory /
--     list_memories tools are wired into that conversation's gateway
--     calls.
--
-- Idempotent — safe to re-run.

-- ---------------------------------------------------------------------------
-- chat.user_memories
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS chat.user_memories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    tenant_id UUID NOT NULL,
    user_id   UUID NOT NULL,

    -- Scope: where does this memory apply?
    --   'global_user'  applies to every conversation for this user
    --   'conversation' only relevant inside scope_id = conversation_id
    --   'artifact'     about a specific file (scope_id = file_id)
    --   'temporary'    expires with context, not persisted long-term
    scope_type VARCHAR(30) NOT NULL DEFAULT 'global_user',
    scope_id   UUID,

    -- Memory classification (6 types — Phase 4 will add inferred_preference)
    --   'explicit_fact'         user explicitly stated ("my name is Beat")
    --   'response_preference'   how user wants AI to respond
    --   'ongoing_interest'      what user is working on
    --   'confirmed_instruction' standing instruction
    --   'inferred_preference'   AI observed pattern (Phase 4)
    --   'artifact_reference'    about a file
    memory_type VARCHAR(40) NOT NULL,
    category    VARCHAR(50),

    content        TEXT NOT NULL,
    -- Lowercased, trimmed, truncated copy of `content` used for dedup
    -- via ExistsByNormalizedKey. ~200 chars is plenty for the duplicate
    -- detection these short fact lines need.
    normalized_key VARCHAR(200),

    -- Provenance
    --   'user_explicit'         user said "remember this"
    --   'assistant_inferred'    AI noticed a pattern (Phase 4)
    --   'conversation_summary'  extracted from conversation summary
    --   'artifact_event'        file was created/modified
    source_type            VARCHAR(30) NOT NULL,
    source_conversation_id UUID,
    source_message_id      UUID,
    -- Short snippet (~200 chars) showing why this memory was created
    evidence_excerpt       TEXT,

    -- Status
    --   'active'                in use
    --   'pending_confirmation'  inferred, needs user approval (Phase 4)
    --   'superseded'            replaced by a newer memory
    --   'deleted'               soft deleted
    status VARCHAR(20) NOT NULL DEFAULT 'active',

    -- Trust & importance
    confidence_score   NUMERIC(4,3) DEFAULT 0.900,
    importance_score   NUMERIC(4,3) DEFAULT 0.500,
    is_user_confirmed  BOOLEAN DEFAULT false,
    is_pinned          BOOLEAN DEFAULT false,

    -- Safety
    --   'normal' | 'restricted' | 'prohibited'
    sensitivity_level VARCHAR(20) NOT NULL DEFAULT 'normal',

    -- Lifecycle / usage tracking
    last_used_at TIMESTAMPTZ,
    use_count    INTEGER DEFAULT 0,

    -- Optional expiry. Memories with valid_until in the past are
    -- treated as inactive by GetActiveForInjection.
    valid_until TIMESTAMPTZ,

    -- Full-text search
    search_vector tsvector,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_user_memories_user_tenant
    ON chat.user_memories(user_id, tenant_id);

CREATE INDEX IF NOT EXISTS idx_user_memories_scope
    ON chat.user_memories(user_id, scope_type)
    WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_user_memories_active
    ON chat.user_memories(user_id)
    WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_user_memories_search
    ON chat.user_memories USING gin(search_vector);

-- Auto-maintain search_vector + updated_at on every write. The
-- search vector uses the 'simple' config because content is mixed
-- Thai + English + identifiers — 'english' stemming butchers Thai,
-- and we don't ship a Thai stemmer.
CREATE OR REPLACE FUNCTION chat.update_memory_search_vector() RETURNS trigger AS $$
BEGIN
    NEW.search_vector := to_tsvector('simple', COALESCE(NEW.content, ''));
    NEW.updated_at := NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS user_memories_search_trigger ON chat.user_memories;
CREATE TRIGGER user_memories_search_trigger
    BEFORE INSERT OR UPDATE OF content ON chat.user_memories
    FOR EACH ROW EXECUTE FUNCTION chat.update_memory_search_vector();

-- ---------------------------------------------------------------------------
-- chat.memory_events
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS chat.memory_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Nullable because some events (e.g. bulk-clear) don't pin to a
    -- single memory row, and a memory id may go away if a hard-delete
    -- ever lands; the audit log survives via memory_id IS NULL.
    memory_id UUID,
    tenant_id UUID NOT NULL,
    user_id   UUID NOT NULL,

    -- 'created' | 'updated' | 'superseded' | 'recalled' |
    -- 'deleted' | 'pinned' | 'unpinned' | 'confirmed' | 'rejected'
    action     VARCHAR(30) NOT NULL,
    -- 'user' | 'assistant' | 'system'
    actor_type VARCHAR(20) NOT NULL,

    reason   TEXT,
    metadata JSONB,

    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_memory_events_memory
    ON chat.memory_events(memory_id);
CREATE INDEX IF NOT EXISTS idx_memory_events_user
    ON chat.memory_events(user_id, tenant_id);

-- ---------------------------------------------------------------------------
-- chat.conversations.memory_enabled
-- ---------------------------------------------------------------------------

ALTER TABLE chat.conversations
    ADD COLUMN IF NOT EXISTS memory_enabled BOOLEAN NOT NULL DEFAULT true;
