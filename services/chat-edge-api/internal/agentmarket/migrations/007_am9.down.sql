-- AM9 down: undoes all of 007 (an image rollback to AM8 reads none of it).

-- ── Per-agent document egress ────────────────────────────────────────────
DROP TABLE IF EXISTS agent_market.agent_egress;

DELETE FROM agent_market.schema_migrations WHERE version = '007_am9';
