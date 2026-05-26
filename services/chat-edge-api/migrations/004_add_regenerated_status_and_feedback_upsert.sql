-- 004_add_regenerated_status_and_feedback_upsert.sql
--
-- Adds the ``regenerated`` value to the message-status enum (so the
-- regenerate endpoint can flip the superseded assistant row without
-- deleting it; keeps the audit trail of older replies intact for
-- timeline scrolling) AND adds a UNIQUE INDEX on
-- ``chat.feedback(message_id, user_id)`` so the feedback endpoint
-- can use ``INSERT ON CONFLICT … DO UPDATE`` for idempotent
-- thumbs-up/thumbs-down toggles.

ALTER TABLE chat.messages_masked
    DROP CONSTRAINT IF EXISTS messages_masked_status_check;

ALTER TABLE chat.messages_masked
    ADD CONSTRAINT messages_masked_status_check
    CHECK (status IN ('pending', 'streaming', 'completed', 'cancelled', 'error', 'regenerated'));

CREATE UNIQUE INDEX IF NOT EXISTS idx_feedback_message_user
    ON chat.feedback(message_id, user_id);
