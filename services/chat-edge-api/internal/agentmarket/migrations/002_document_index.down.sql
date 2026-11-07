-- Undoes 002 alone: the duplicate index, the two columns it added and its
-- migration record (so starting an AM2 image again applies it afresh). The
-- AM1 code reads neither column, so an image rollback to AM1c does not need
-- this; the whole feature's removal is 001's DROP SCHEMA agent_market CASCADE,
-- which takes all of it.
DROP TABLE IF EXISTS agent_market.document_index;
ALTER TABLE agent_market.agent_installs DROP COLUMN IF EXISTS previous_definition_id;
ALTER TABLE agent_market.agent_runs DROP COLUMN IF EXISTS scratch_purged_at;
DELETE FROM agent_market.schema_migrations WHERE version = '002_document_index';
