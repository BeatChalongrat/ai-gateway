-- 007_create_user_preferences.sql
--
-- Per-user "custom instructions" — the ChatGPT-style two-prompt
-- preferences pair that gets prepended to every conversation's
-- system prompt before file context.
--
-- Single row per (tenant_id, user_id). Upserted via PUT; missing
-- row defaults to empty strings.

CREATE TABLE IF NOT EXISTS chat.user_preferences (
    id                            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id                     UUID NOT NULL,
    user_id                       UUID NOT NULL,
    custom_instructions_about     TEXT NOT NULL DEFAULT '',
    custom_instructions_respond   TEXT NOT NULL DEFAULT '',
    updated_at                    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_user_prefs_owner
    ON chat.user_preferences (tenant_id, user_id);
