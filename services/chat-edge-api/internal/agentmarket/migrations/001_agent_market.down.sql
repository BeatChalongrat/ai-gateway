-- Removes Agent Market's data entirely. The core schemas are untouched:
-- nothing outside agent_market references anything inside it.
DROP SCHEMA IF EXISTS agent_market CASCADE;
