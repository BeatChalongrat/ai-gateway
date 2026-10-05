-- AM10.
--
-- Same isolation contract as 001–007: every table lives in schema
-- agent_market, no key points into or out of it, no core table is altered.

-- ── Chats under each agent (section D) ───────────────────────────────────
--
-- A chat can be named, pinned and deleted. Deleting a chat takes it off the
-- list and nothing else: its rounds, their files, the run pages and the audit
-- rows live on (a tidy sidebar is not a reason to lose a working paper), so
-- it is a date, never a DELETE. The install's first chat has no row of its
-- own (thread_id NULL, "main"), so its name, pin and deletion live on the
-- install.
ALTER TABLE agent_market.agent_threads
    ADD COLUMN IF NOT EXISTS title      text,
    ADD COLUMN IF NOT EXISTS pinned_at  timestamptz,
    ADD COLUMN IF NOT EXISTS deleted_at timestamptz;
ALTER TABLE agent_market.agent_installs
    ADD COLUMN IF NOT EXISTS first_chat_title      text,
    ADD COLUMN IF NOT EXISTS first_chat_pinned_at  timestamptz,
    ADD COLUMN IF NOT EXISTS first_chat_deleted_at timestamptz;

-- ── A form for this round only (section E) ───────────────────────────────
--
-- A customer's form dropped into a round is used for that round and never
-- becomes the install's: the round carries it (the file, its mapping and
-- what came of it), beside the install's saved form, which stays as it is.
ALTER TABLE agent_market.agent_runs ADD COLUMN IF NOT EXISTS round_template jsonb;
-- The form file is the round's, and not one of its inputs (never parsed as data).
ALTER TABLE agent_market.agent_files DROP CONSTRAINT IF EXISTS agent_files_status_check;
ALTER TABLE agent_market.agent_files ADD CONSTRAINT agent_files_status_check
    CHECK (status IN ('staged', 'active', 'pending_decision', 'next_round', 'removed', 'form'));
-- The thread shows one item that follows the form (schema.go declares what of it is sent).
ALTER TABLE agent_market.agent_thread_items DROP CONSTRAINT IF EXISTS agent_thread_items_kind_check;
ALTER TABLE agent_market.agent_thread_items ADD CONSTRAINT agent_thread_items_kind_check
    CHECK (kind IN ('greeting', 'user_text', 'user_files', 'agent_text', 'card', 'progress', 'outputs', 'notice', 'intent', 'email_draft', 'form'));
