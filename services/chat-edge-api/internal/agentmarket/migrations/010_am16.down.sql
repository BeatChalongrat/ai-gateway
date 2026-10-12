-- AM16 down: the masking switch and the mark beside the leave switch go; a row made for masking alone
-- (leave_set = false) said nothing about leaving and goes with them, so the agent is back on its default.
DELETE FROM agent_market.agent_egress WHERE NOT leave_set;
ALTER TABLE agent_market.agent_egress DROP COLUMN IF EXISTS leave_set;
ALTER TABLE agent_market.agent_egress DROP COLUMN IF EXISTS masked;
DELETE FROM agent_market.schema_migrations WHERE version = '010_am16';
