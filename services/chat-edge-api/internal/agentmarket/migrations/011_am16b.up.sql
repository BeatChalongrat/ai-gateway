-- AM16 — who outside the organisation an agent's documents may reach.
--
-- "Documents may leave" said yes or no. It did not say to whom: with it on, every model of the catalogue
-- was open — and behind a router a model's name does not say who runs it (13 Oct 2026: a request for
-- Claude through OpenRouter was answered by Amazon, one for GPT by Microsoft). The admin now names the
-- recipients, each a route and the company that runs the model behind it:
--
--   org_recipients     the organisation's own list — the ceiling for every agent
--   agent_recipients   one agent's list under it. It only ever narrows: a recipient counts for an agent
--                      when it is on both.
--
-- Nothing ticked is nobody. So nothing changes for an agent by this migration, except that an agent
-- whose documents may leave reaches no model outside until an admin has named who — no agent on live
-- has that switch on.

CREATE TABLE IF NOT EXISTS agent_market.org_recipients (
    org_id     text        NOT NULL,
    recipient  text        NOT NULL,
    updated_by text        NOT NULL DEFAULT '',
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, recipient)
);

CREATE TABLE IF NOT EXISTS agent_market.agent_recipients (
    org_id     text        NOT NULL,
    slug       text        NOT NULL,
    recipient  text        NOT NULL,
    updated_by text        NOT NULL DEFAULT '',
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, slug, recipient)
);
