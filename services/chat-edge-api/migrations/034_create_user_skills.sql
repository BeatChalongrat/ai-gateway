-- 034_create_user_skills.sql
--
-- FIX12 Item 3 — user SKILLS (server-side, on chat-edge).
--
-- A "skill" is a named, reusable block of INSTRUCTION TEXT the user
-- authors once and the AI applies when it is relevant. Skills are
-- string-concatenated into the system prompt (when a trigger matches
-- the user's latest message) or returned verbatim as a use_skill tool
-- result. That is the WHOLE contract.
--
-- HARD SECURITY INVARIANT — instruction-only, NEVER executable:
--   A skill carries NO command / exec / entrypoint / script field, and
--   no code path anywhere runs a skill's content. `instructions` is
--   plain text handed to the model, exactly like a saved memory or a
--   project instruction block. There is deliberately no column that
--   could be interpreted as something to run. Because a skill can never
--   reach an executor or an egress channel, it needs no connector-style
--   egress review.
--
-- Every read/write is hard-scoped by (tenant_id, user_id) in the repo;
-- a skill is private to the user who created it.
--
-- Idempotent — safe to re-run.

CREATE TABLE IF NOT EXISTS chat.user_skills (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    tenant_id UUID NOT NULL,
    user_id   UUID NOT NULL,

    -- Human-facing name. Unique per user (case-insensitive) so
    -- use_skill can resolve a skill by name without ambiguity.
    name VARCHAR(80) NOT NULL,

    -- Short one-line summary shown in the use_skill catalog.
    description VARCHAR(500) NOT NULL DEFAULT '',

    -- Case-insensitive substring cues. When any trigger appears in the
    -- user's latest message the skill's instructions are auto-injected
    -- into the system prompt for that turn.
    triggers TEXT[] NOT NULL DEFAULT '{}',

    -- The instruction text itself. Plain prose handed to the model —
    -- NOT a command, NOT a script, NEVER executed.
    instructions TEXT NOT NULL,

    enabled BOOLEAN NOT NULL DEFAULT true,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Size caps mirrored in the repo (defensive) so a bad client can't
    -- plant an oversized prompt block.
    CONSTRAINT user_skills_instructions_len CHECK (length(instructions) BETWEEN 1 AND 10000),
    CONSTRAINT user_skills_name_len         CHECK (length(name) BETWEEN 1 AND 80)
);

-- One skill name per user, case-insensitive.
CREATE UNIQUE INDEX IF NOT EXISTS idx_user_skills_name
    ON chat.user_skills(tenant_id, user_id, lower(name));

-- The turn-time read path: all enabled skills for a user.
CREATE INDEX IF NOT EXISTS idx_user_skills_user_enabled
    ON chat.user_skills(tenant_id, user_id, enabled);

-- Auto-maintain updated_at on every write.
CREATE OR REPLACE FUNCTION chat.update_user_skills_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS user_skills_updated_at_trigger ON chat.user_skills;
CREATE TRIGGER user_skills_updated_at_trigger
    BEFORE UPDATE ON chat.user_skills
    FOR EACH ROW EXECUTE FUNCTION chat.update_user_skills_updated_at();
