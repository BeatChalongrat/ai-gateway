-- AM12 down: undoes all of 009 (an image rollback to AM11 reads none of it).

DROP TABLE IF EXISTS agent_market.web_calls;
DROP TABLE IF EXISTS agent_market.web_policy;
DROP TABLE IF EXISTS agent_market.org_suppliers;
DROP TABLE IF EXISTS agent_market.web_deny_hosts;

DELETE FROM agent_market.schema_migrations WHERE version = '009_am12';
