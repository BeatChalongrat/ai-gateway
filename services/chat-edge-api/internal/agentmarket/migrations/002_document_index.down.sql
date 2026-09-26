-- Removes the AM2 duplicate index alone (the whole feature's removal is
-- 001's DROP SCHEMA agent_market CASCADE, which takes this table with it).
DROP TABLE IF EXISTS agent_market.document_index;
