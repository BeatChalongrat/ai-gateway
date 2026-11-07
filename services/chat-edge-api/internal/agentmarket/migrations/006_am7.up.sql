-- AM7.
--
-- Same isolation contract as 001–005: every table lives in schema
-- agent_market, no key points into or out of it, no core table is altered.
-- AM7 adds no setting to the organisation's row: the admin app reads and
-- writes org_settings as the chat app did. It adds the mail relay.

-- ── The mail relay (phase 5) ─────────────────────────────────────────────
--
-- The organisation connects its own relay: host, port, TLS mode, username
-- and sender address, kept by an admin on the admin app's Agent Market page.
-- The password is not here: it lives in .env (AGENT_MARKET_SMTP_PASSWORD)
-- and is never rendered, logged or stored in the database. No row, or a row
-- without a host and a sender, is "not set up": every send says so and the
-- draft stays there to copy.
CREATE TABLE IF NOT EXISTS agent_market.mail_settings (
    org_id     text PRIMARY KEY,
    host       text NOT NULL DEFAULT '',
    port       integer NOT NULL DEFAULT 587 CHECK (port BETWEEN 1 AND 65535),
    tls_mode   text NOT NULL DEFAULT 'starttls' CHECK (tls_mode IN ('starttls', 'tls', 'none')),
    username   text NOT NULL DEFAULT '',
    sender     text NOT NULL DEFAULT '',
    updated_by text NOT NULL DEFAULT '',
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Reminders ride the same relay: a notification goes to its person by
-- e-mail at most once — sent, failed, or skipped (no relay set up when it
-- was written; it is never sent later).
ALTER TABLE agent_market.notifications ADD COLUMN IF NOT EXISTS email_status text
    CHECK (email_status IN ('sent', 'failed', 'skipped'));
ALTER TABLE agent_market.notifications ADD COLUMN IF NOT EXISTS emailed_at timestamptz;
CREATE INDEX IF NOT EXISTS notifications_to_email ON agent_market.notifications (created_at)
    WHERE email_status IS NULL AND dismissed_at IS NULL;
