# Agent Market — architecture (AM1)

Installable agents that work in their own chat workspace, ask the user back
when the data is unclear, and deliver a report, a deck and tables. AM1 ships
the spine and agent 9 (Financial Report + Executive Deck). Removal:
[REMOVAL.md](REMOVAL.md).

## Two planes

```
 browser / desktop ── web-chat (features/agent-market) ── chat-edge  ── control plane
                                                          │ internal/agentmarket (only when AGENT_MARKET_ENABLED)
                                                          │ own pgx pool "chat-edge-agent-market" (3 conns)
                                                          │ writes rows · reads rows · never runs a step
                                                          ▼
                                              Postgres schema agent_market  ◀── the contract (run row + queue)
                                                          ▲
                                                          │ own pool "agent-runner" (4 conns) · leases
 agent-runner (own container, profile agent-market) ──────┘ execution plane
   · claims runs (FOR UPDATE SKIP LOCKED), lane = AGENT_RUNNER_SLOTS
   · each step → a fresh ai-gateway-sandbox container (--network none, uid 65534,
     read-only rootfs, cpu/mem/pids/wall caps) with the engine zip read-only
   · model calls → gateway with its OWN API key; ≤1 in flight; yields to chat
   · files → object storage prefix agent-market/ (the runner mounts only that folder)
```

- **A run is a job.** `POST /messages` returns at once; the UI follows the
  thread through `GET /installs/{id}/events?after=<rev>` (SSE, resumable
  from any cursor). No HTTP request waits for a run.
- **States:** `queued → running → awaiting_input → running → succeeded | failed | cancelled`.
  `awaiting_input` is durable and has no timeout; it holds no slot and spends no budget.
- **Cancel** is idempotent (a second cancel returns the same state).

## Steps and resume

`parse (per file) → preflight → clarify → compute → postcompute → clarify → narrative → render (+ quality gate) → publish`

Every step writes an `agent_run_steps` row keyed by an idempotency key made
of everything that decides its output (file hashes, answers, period,
defaults, formats, theme version). A resumed run walks the same list: a key
already done is read back, never re-run; a step whose inputs changed runs as a
new step. A runner killed mid-step leaves the step `running` with an expiring
lease; the next runner re-executes exactly that step. Artifacts are inserted
idempotently on `(produced_by_step, format)` and stored under deterministic
keys, so a retry never makes a second file.

Large outputs (`parsed.json` per file, `computed.json`) are blobs in object
storage; steps pass them to the next sandbox as read-only inputs.

## The clarification engine (`internal/agentmarket/clarify.go`)

Rule zero: the model never decides what to ask. Package checks
(`backend/agent_market/packages/*/checks/`) return findings
`{key, code, severity, question, options[2..4], default, evidence, rememberable}`.
The engine batches them per moment (preflight, postcompute): ≤4 questions per
card, blocking first; leftover cosmetic ones take their default and become
assumptions; a blocking one is never defaulted (it waits for the next card);
a key is never asked twice in a run; a remembered install default answers
silently and is written as an assumption. Free-text answers are read by
keywords, then by a model call that may only pick one of the card's own
options; after two failed re-asks the default is used and recorded.

Cards live twice: as a row (`agent_run_clarifications`) and as a thread item
the web renders. Every status change moves both in one transaction — a
cancel, a new round (which closes every card still open on earlier runs,
late-file and format cards included) — so no closed card stays clickable. A
cosmetic question runs on its default until answered; the person's answer
(click, sentence or skip) replaces that "default for now" assumption, so the
files never say a default was used for something the person chose.

## Deterministic compute and deliverables

- `scripts/metrics.py` computes every number with `Decimal`; each number
  carries a citation (file · sheet · cells · script · formula).
- The narrative is the only model step. A guard refuses any number in the
  model's text that is not one of the computed table's pre-formatted strings
  (then the fallback model, then a rules-written narrative — never an empty one).
  Account codes from the file's tables (`quotable_ids`) are identifiers, not
  figures: "บัญชี 5300-01" passes, an invented code or amount does not.
- The run's date ("จัดทำ …", the year of a named month) is Thai time; the
  runner's own clock is UTC.
- `deliverables/` renders report (.docx, Sarabun embedded), deck (.pptx,
  built with `pptx_generator.py`'s builders + a theme layer), tables (.xlsx
  with a SUMIFS check sheet) and PDF from **one** content model; the quality
  gate (`quality.py`, using `doc_validator.py` and `pptx_inspector.py`) fails
  the step on overflow, off-theme fonts, unbound charts, empty required
  sections, blank citations, or missing assumptions.
- Adding a format later re-renders from the stored computed table: no compute
  step runs, the version is bumped.

## Bulkheads

| Risk | Bulkhead |
|---|---|
| runner crash / OOM / runaway | own container + limits; per-step sandbox with its own caps; leases resume the run |
| DB exhaustion | own pools (runner 4, control plane 3); chat's pool untouched; schema isolated |
| model spend / rate | own gateway API key (portal project "Agent Market runner"); per-install monthly budget in credits, charged what the platform paid: every attempt a model answered (a refused one too), nothing for a gateway cache hit (the gateway replays the original cost in the body) |
| starving chat | runner lane (`AGENT_RUNNER_SLOTS`, default 1); ≤1 agent model call in flight; before every model call the runner reads the backend's `/ready` and waits while any chat turn is generating or queued, or the event loop lags (bounded by `AGENT_RUNNER_YIELD_MAX_SECONDS`, default 300) — an agent generation never runs next to a person's |
| core ops tooling | separate compose project; no autoheal label; not in build-images.sh; ops-watch never sees it |

## Package format

`packages/<name>/`: `agent.json` (manifest), `AGENT.md` (narrative
instructions), `checks/`, `scripts/`, `templates/`, `sample-data/`,
`self-test.json`, `evals/` (cases + fixtures, not shipped in the runtime zip).
The runtime zip is built deterministically; the package checksum is computed
over the same bytes and stored on every install and run.
