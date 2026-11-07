-- AM9.
--
-- Same isolation contract as 001–006: every table lives in schema
-- agent_market, no key points into or out of it, no core table is altered.

-- ── Per-agent document egress (section D) ────────────────────────────────
--
-- The organisation's switch (org_settings.documents_may_leave) stays the
-- ceiling. Each agent has a setting under it that can only be stricter: a
-- round's documents may go to a model outside the organisation only when the
-- organisation and the agent both allow it. No row is the agent's default:
-- off for an agent that touches accounting (its manifest's category is
-- "finance"), otherwise the organisation's switch alone decides. Every change
-- is an admin's, written to the admin audit log before it is kept.
CREATE TABLE IF NOT EXISTS agent_market.agent_egress (
    org_id              text NOT NULL,
    slug                text NOT NULL,
    documents_may_leave boolean NOT NULL,
    updated_by          text NOT NULL DEFAULT '',
    updated_at          timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, slug)
);

-- ── "Send as me" (section I) ─────────────────────────────────────────────
--
-- An approved message goes from the person's own mailbox (the mail
-- connector, through the MCP gateway) or through the organisation's relay on
-- their behalf. The log says which carried it. Sends before AM9 went by the
-- relay (NULL reads as "organisation").
ALTER TABLE agent_market.email_sends ADD COLUMN IF NOT EXISTS transport text
    CHECK (transport IN ('mailbox', 'organisation'));
