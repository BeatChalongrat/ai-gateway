-- 008_create_shared_conversations.sql
--
-- Public-read-only share links for conversations. Token is a
-- 22-char URL-safe base64 (~128 bits of entropy), generated server-
-- side via crypto/rand.
--
-- ``expires_at`` NULL means "never expires" (per-user opt-in).
-- ``tenant_id`` is stored verbatim so an authorized revoke check can
-- gate without re-joining to chat.conversations.

CREATE TABLE IF NOT EXISTS chat.shared_conversations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES chat.conversations(id) ON DELETE CASCADE,
    tenant_id       UUID NOT NULL,
    share_token     VARCHAR(32) NOT NULL UNIQUE,
    expires_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_shared_conv_token        ON chat.shared_conversations(share_token);
CREATE INDEX IF NOT EXISTS idx_shared_conv_conversation ON chat.shared_conversations(conversation_id);
