-- AM10 down: undoes all of 008 (an image rollback to AM9 reads none of it).

-- ── Chats under each agent ───────────────────────────────────────────────
ALTER TABLE agent_market.agent_threads
    DROP COLUMN IF EXISTS title,
    DROP COLUMN IF EXISTS pinned_at,
    DROP COLUMN IF EXISTS deleted_at;
ALTER TABLE agent_market.agent_installs
    DROP COLUMN IF EXISTS first_chat_title,
    DROP COLUMN IF EXISTS first_chat_pinned_at,
    DROP COLUMN IF EXISTS first_chat_deleted_at;

-- ── A form for this round only ───────────────────────────────────────────
ALTER TABLE agent_market.agent_runs DROP COLUMN IF EXISTS round_template;

DELETE FROM agent_market.schema_migrations WHERE version = '008_am10';
