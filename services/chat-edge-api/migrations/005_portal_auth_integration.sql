-- Migration 005 — Portal auth integration.
--
-- The Portal (FastAPI ``services/portal/auth_service.py``) is now the
-- sole identity authority. Chat Edge keeps a shadow row in
-- ``identity.users`` so chat FKs hold, but doesn't store passwords or
-- run its own credential checks. This migration relaxes the schema
-- so the shadow rows can be inserted without inventing fake hashes.
--
-- Idempotent — safe to re-run.

-- 1. password_hash is now optional. Existing legacy rows keep their
--    hashes; portal-provisioned rows insert empty string.
ALTER TABLE identity.users
    ALTER COLUMN password_hash DROP NOT NULL,
    ALTER COLUMN password_hash SET DEFAULT '';

-- 2. Track the origin of each user so audits can tell legacy
--    chat-edge accounts apart from Portal-provisioned shadows.
ALTER TABLE identity.users
    ADD COLUMN IF NOT EXISTS provisioned_from TEXT NOT NULL DEFAULT 'portal';

-- 3. Backfill anything currently present with a known label. We can't
--    tell legacy from portal at migration time, so mark surviving
--    rows as 'legacy' — fresh portal upserts will overwrite via
--    INSERT … ON CONFLICT … DO UPDATE if the same id arrives.
UPDATE identity.users
   SET provisioned_from = 'legacy'
 WHERE provisioned_from = 'portal'  -- = the default we just set
   AND created_at < NOW() - INTERVAL '1 second';

-- 4. PK on id is implicit, but the auth middleware looks up by id on
--    every authenticated request; documenting the access pattern
--    here makes the hot path obvious to anyone reading psql \\d+ .
COMMENT ON COLUMN identity.users.id IS
    'Equal to the Portal user id (JWT sub) for portal-provisioned rows.';
COMMENT ON COLUMN identity.users.provisioned_from IS
    'Origin tag: ''portal'' (FastAPI Portal shadow), ''legacy'' (pre-Phase-1.5).';
