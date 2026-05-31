-- Migration 020: Model lock per conversation (Fix 5).
--
-- When a conversation's model_id is the "auto" sentinel the resolver
-- picks a concrete model PER REQUEST, which let the model swap mid-chat
-- (e.g. simple turn -> Claude Sonnet, complex turn -> o3-mini) and made
-- follow-up behaviour inconsistent. locked_model pins the model chosen
-- on the first auto turn so every later turn reuses it. Cleared when the
-- user changes the model picker (see ConversationRepo.UpdateModel) so a
-- switch back to "auto" re-resolves fresh. Empty string = not locked.
--
-- Explicit (non-auto) model_id picks were always stable and don't need
-- the lock; this only de-flakes the "auto" path.

BEGIN;

ALTER TABLE chat.conversations
    ADD COLUMN IF NOT EXISTS locked_model TEXT NOT NULL DEFAULT '';

COMMIT;
