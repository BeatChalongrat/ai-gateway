-- AM16 — masking is the admin's own switch per agent, beside "documents may leave".
--
-- Until now masking followed the leave switch (AM13b): an agent whose documents could leave was masked,
-- one whose documents could not was sent to the in-house model as typed. The two are separated:
--
--   masked     personal data in the text this agent sends to a model is masked first. Off by default.
--   leave_set  an admin has said whether this agent's documents may leave. A row can now be made by
--              setting masking alone; such a row says nothing about leaving, and the agent keeps the
--              default for that (off).
--
-- Nothing changes for an agent by this migration: a row that exists was made by an admin setting the
-- leave switch (leave_set = true), and where that switch is on the agent was masked until now — so it
-- stays masked until an admin turns that off.

ALTER TABLE agent_market.agent_egress ADD COLUMN IF NOT EXISTS masked boolean NOT NULL DEFAULT false;
ALTER TABLE agent_market.agent_egress ADD COLUMN IF NOT EXISTS leave_set boolean NOT NULL DEFAULT true;

UPDATE agent_market.agent_egress SET masked = true WHERE documents_may_leave AND NOT masked;
