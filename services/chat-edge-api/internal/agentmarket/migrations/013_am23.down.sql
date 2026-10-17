-- AM23 down: the switch goes; every document carries the neutral line again where a model drafted its words.
ALTER TABLE agent_market.org_settings DROP COLUMN IF EXISTS assistant_line;
DELETE FROM agent_market.schema_migrations WHERE version = '013_am23';
