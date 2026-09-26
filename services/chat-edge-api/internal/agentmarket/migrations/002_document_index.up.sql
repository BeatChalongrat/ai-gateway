-- AM2: the per-install duplicate index (Invoice Processing, and any package
-- whose manifest declares "document_index").
--
-- One row per document a succeeded run recorded: the supplier tax ID, the
-- document number, the date and the total — what the duplicate check
-- compares — plus the file's name and sha256 so a question can say which
-- earlier file it matched. The runner hands an install's own rows to the
-- package as the run's history; never another install's, never a
-- self-test's (self-tests write none).
--
-- Retention: expires_at = recorded + the manifest's retention_days
-- (Invoice Processing: 730); the runner deletes expired rows every hour.
-- Removing the install deletes its rows at once, and so does deleting the
-- account (the Agent Market export/delete provider). Same isolation
-- contract as 001: schema agent_market only, no key into or out of it.

CREATE TABLE IF NOT EXISTS agent_market.document_index (
    id              bigserial PRIMARY KEY,
    install_id      uuid NOT NULL REFERENCES agent_market.agent_installs (id) ON DELETE CASCADE,
    tenant_id       uuid NOT NULL,
    user_id         uuid NOT NULL,
    run_id          uuid NOT NULL,
    doc_key         text NOT NULL,
    name            text NOT NULL,
    sha256          text NOT NULL,
    seller_tax_id   text,
    document_number text,
    document_date   date,
    total           numeric(18, 2),
    created_at      timestamptz NOT NULL DEFAULT now(),
    expires_at      timestamptz NOT NULL,
    UNIQUE (install_id, run_id, doc_key)
);
CREATE INDEX IF NOT EXISTS document_index_install ON agent_market.document_index (install_id, created_at DESC);
CREATE INDEX IF NOT EXISTS document_index_expiry ON agent_market.document_index (expires_at);
CREATE INDEX IF NOT EXISTS document_index_owner ON agent_market.document_index (tenant_id, user_id);

-- AM2 retention: a failed or cancelled run's scratch (step blobs) is deleted
-- about 7 days after it ended; this marks the runs already cleaned so the
-- hourly pass does not scan them again (an agent_market table: the isolation
-- contract forbids altering core tables only).
ALTER TABLE agent_market.agent_runs ADD COLUMN IF NOT EXISTS scratch_purged_at timestamptz;
