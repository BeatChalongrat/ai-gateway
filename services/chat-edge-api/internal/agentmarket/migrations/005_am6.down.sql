-- AM6 down: undoes all of 005 (an image rollback to AM5 reads none of it).
-- Each section undoes exactly what its part of the up file did.

-- ── E-mail drafts and sends (section C) ──────────────────────────────────
-- The draft's thread items go with the drafts (AM5 has no such kind).
DELETE FROM agent_market.agent_thread_items WHERE kind = 'email_draft';
ALTER TABLE agent_market.agent_thread_items DROP CONSTRAINT IF EXISTS agent_thread_items_kind_check;
ALTER TABLE agent_market.agent_thread_items ADD CONSTRAINT agent_thread_items_kind_check
    CHECK (kind IN ('greeting', 'user_text', 'user_files', 'agent_text', 'card', 'progress', 'outputs', 'notice', 'intent'));
DROP TABLE IF EXISTS agent_market.email_sends;
DROP TABLE IF EXISTS agent_market.email_drafts;

-- ── Reminders and notifications (section B) ──────────────────────────────
-- The rounds a reminder opened are ordinary rounds and stay.
DROP TABLE IF EXISTS agent_market.notifications;
DROP TABLE IF EXISTS agent_market.reminders;

-- ── Charging follows delivery (section F) ────────────────────────────────
-- A voided row goes back to being a plain row of the ledger, as AM5 counts
-- it: the run is charged what it cost again.
UPDATE agent_market.agent_runs SET credits_spent = credits_cost WHERE credits_cost > credits_spent;
ALTER TABLE agent_market.agent_runs DROP COLUMN IF EXISTS delivered_at;
ALTER TABLE agent_market.agent_runs DROP COLUMN IF EXISTS gate_open;
ALTER TABLE agent_market.agent_runs DROP COLUMN IF EXISTS credits_cost;
DROP INDEX IF EXISTS agent_market.agent_budget_charged;
ALTER TABLE agent_market.agent_budget_ledger DROP COLUMN IF EXISTS void_reason;
ALTER TABLE agent_market.agent_budget_ledger DROP COLUMN IF EXISTS voided_at;

-- ── What a round records, and a receipt confirmed in this system (section A) ──
-- The detail of the entries goes; the entries stay, as AM5 reads them.
ALTER TABLE agent_market.document_index DROP COLUMN IF EXISTS detail;

DELETE FROM agent_market.schema_migrations WHERE version = '005_am6';
