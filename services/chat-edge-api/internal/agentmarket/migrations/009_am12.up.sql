-- AM12.
--
-- Same isolation contract as 001–008: every table lives in schema
-- agent_market, no key points into or out of it, no core table is altered.

-- ── Sites an admin says are never to be opened ───────────────────────────
--
-- An agent that works on the web chooses its own sources (the model picks
-- from what a search returned). There is no list of sites it may use. The
-- only list an admin keeps points the other way: hosts that are never
-- opened, whatever was picked. A host also covers its sub-domains.
CREATE TABLE IF NOT EXISTS agent_market.web_deny_hosts (
    org_id   text NOT NULL,
    host     text NOT NULL,
    note     text NOT NULL DEFAULT '',
    added_by text NOT NULL DEFAULT '',
    added_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, host)
);

-- ── The organisation's own suppliers ─────────────────────────────────────
--
-- Who the organisation already buys from. A round is told this list and
-- prefers a supplier on it that carries the thing, without being limited to
-- it. A seller is "on the list" only by a row here — an admin's — never
-- because a web page said so.
CREATE TABLE IF NOT EXISTS agent_market.org_suppliers (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id     text NOT NULL,
    name       text NOT NULL,
    tax_id     text NOT NULL DEFAULT '',
    website    text NOT NULL DEFAULT '',
    note       text NOT NULL DEFAULT '',
    added_by   text NOT NULL DEFAULT '',
    added_at   timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (org_id, name)
);

-- ── What a round on the web may use, and how long unused pages are kept ──
--
-- No row: the agent's own caps (its manifest) and seven days. A row can only
-- lower a cap below the agent's own.
CREATE TABLE IF NOT EXISTS agent_market.web_policy (
    org_id           text PRIMARY KEY,
    max_searches     integer CHECK (max_searches BETWEEN 0 AND 20),
    max_pages        integer CHECK (max_pages BETWEEN 1 AND 20),
    max_seconds      integer CHECK (max_seconds BETWEEN 30 AND 1800),
    max_source_mb    integer CHECK (max_source_mb BETWEEN 1 AND 10),
    unused_keep_days integer NOT NULL DEFAULT 7 CHECK (unused_keep_days BETWEEN 1 AND 90),
    updated_by       text NOT NULL DEFAULT '',
    updated_at       timestamptz NOT NULL DEFAULT now()
);

-- ── Every search made and every address a round tried to open ────────────
--
-- One row per search and per fetch: where, when, what came of it. This is
-- what the admin's page reads to say which hosts a run actually reached, and
-- what the retention pass reads: the stored page of a source that became a
-- candidate is the run's evidence and is kept with the run; the stored page
-- of one that did not (purge_after) is deleted after the policy's days. A
-- fetch that was refused stored nothing.
CREATE TABLE IF NOT EXISTS agent_market.web_calls (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          text NOT NULL,
    install_id      uuid NOT NULL,
    run_id          uuid NOT NULL REFERENCES agent_market.agent_runs(id) ON DELETE CASCADE,
    kind            text NOT NULL CHECK (kind IN ('search', 'fetch')),
    step_key        text NOT NULL,
    source          text NOT NULL DEFAULT '',
    host            text NOT NULL DEFAULT '',
    url             text NOT NULL DEFAULT '',
    outcome         text NOT NULL DEFAULT '',
    reason          text NOT NULL DEFAULT '',
    http_status     integer,
    bytes           bigint NOT NULL DEFAULT 0,
    results         integer NOT NULL DEFAULT 0,
    provider        text NOT NULL DEFAULT '',
    credits         numeric(18, 4) NOT NULL DEFAULT 0,
    started_at      timestamptz NOT NULL DEFAULT now(),
    duration_ms     integer NOT NULL DEFAULT 0,
    snapshot_sha256 text NOT NULL DEFAULT '',
    blob_prefix     text NOT NULL DEFAULT '',
    used            boolean,
    purge_after     timestamptz,
    purged_at       timestamptz,
    UNIQUE (run_id, step_key)
);
CREATE INDEX IF NOT EXISTS web_calls_org_time ON agent_market.web_calls (org_id, started_at DESC);
CREATE INDEX IF NOT EXISTS web_calls_purge ON agent_market.web_calls (purge_after) WHERE purge_after IS NOT NULL AND purged_at IS NULL;
