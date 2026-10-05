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
UPDATE agent_market.agent_files SET status = 'removed' WHERE status = 'form';
ALTER TABLE agent_market.agent_files DROP CONSTRAINT IF EXISTS agent_files_status_check;
ALTER TABLE agent_market.agent_files ADD CONSTRAINT agent_files_status_check
    CHECK (status IN ('staged', 'active', 'pending_decision', 'next_round', 'removed'));
DELETE FROM agent_market.agent_thread_items WHERE kind = 'form';
ALTER TABLE agent_market.agent_thread_items DROP CONSTRAINT IF EXISTS agent_thread_items_kind_check;
ALTER TABLE agent_market.agent_thread_items ADD CONSTRAINT agent_thread_items_kind_check
    CHECK (kind IN ('greeting', 'user_text', 'user_files', 'agent_text', 'card', 'progress', 'outputs', 'notice', 'intent', 'email_draft'));

DELETE FROM agent_market.schema_migrations WHERE version = '008_am10';
