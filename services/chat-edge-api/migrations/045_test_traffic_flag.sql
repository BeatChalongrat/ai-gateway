-- FIX119 Part C2 — test traffic is labelled where it is written.
--
-- Probe and test accounts (fix48-probe@local and friends) are ordinary
-- rows: auto-provisioned on first request, indistinguishable from a
-- person's in every count the admin analytics compute. FIX118 measured
-- the cost of not labelling: 176 assistant rows nobody could tell from
-- traffic.
--
-- is_test is decided ONCE, when the user row is provisioned, from the
-- caller's email against TEST_ACCOUNT_EMAIL_PATTERN (chat-edge config,
-- default `@local$`), and copied onto every conversation and message the
-- user creates or imports, so the analytics queries stay a single
-- predicate (`AND NOT is_test`) with no join. Nothing here changes an
-- existing row: the backfill for rows written before this migration is a
-- separate, owner-run script (scripts/backfill_is_test.sql).
ALTER TABLE identity.users
    ADD COLUMN IF NOT EXISTS is_test BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE chat.conversations
    ADD COLUMN IF NOT EXISTS is_test BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE chat.messages_masked
    ADD COLUMN IF NOT EXISTS is_test BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN identity.users.is_test IS
    'FIX119 — the account matched TEST_ACCOUNT_EMAIL_PATTERN when provisioned; its traffic is excluded from analytics by default';
COMMENT ON COLUMN chat.conversations.is_test IS
    'FIX119 — copied from identity.users.is_test at creation/import';
COMMENT ON COLUMN chat.messages_masked.is_test IS
    'FIX119 — copied from the conversation at creation/import';
