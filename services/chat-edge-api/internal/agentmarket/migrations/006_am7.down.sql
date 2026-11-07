-- AM7 down: undoes all of 006 (an image rollback to AM6 reads none of it).

-- ── The mail relay (phase 5) ─────────────────────────────────────────────
DROP INDEX IF EXISTS agent_market.notifications_to_email;
ALTER TABLE agent_market.notifications DROP COLUMN IF EXISTS emailed_at;
ALTER TABLE agent_market.notifications DROP COLUMN IF EXISTS email_status;
DROP TABLE IF EXISTS agent_market.mail_settings;

DELETE FROM agent_market.schema_migrations WHERE version = '006_am7';
