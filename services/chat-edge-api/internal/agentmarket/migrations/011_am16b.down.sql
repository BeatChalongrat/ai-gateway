-- AM16 down: the two lists of recipients go. An agent whose documents may leave is then open to every
-- model of the catalogue again, as before — turn its switch off first if that is not wanted.
DROP TABLE IF EXISTS agent_market.agent_recipients;
DROP TABLE IF EXISTS agent_market.org_recipients;
DELETE FROM agent_market.schema_migrations WHERE version = '011_am16b';
