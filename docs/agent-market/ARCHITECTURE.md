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
  with a SUMIFS check sheet) from **one** content model (no PDF since AM1b:
  its Thai shaping was wrong; it returns in AM-2 as the finished .docx
  converted by headless LibreOffice); the quality
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

## Self-serve (AM3)

- **The organisation** sits in front of every route (`org.go`): each request
  resolves the person's access — the org-wide switch (`off` / `list` /
  `everyone`), their row on the list, their role (`member` / `publisher` /
  `admin`) and whom to ask. Break-glass admins come from
  `AGENT_MARKET_ADMIN_EMAILS`. A run shared with them is readable with a
  sign-in alone (`/shared/*`), even without access of their own.
- **Budgets** (`budget.go`) are checked by the runner before every costly
  step: the person's month, their department's, the install's. One that runs
  out parks the run like a question does — `awaiting_input`, no slot, no
  spend — with a notice naming who to ask; the hourly pass and every admin
  budget change resume what has room again.
- **The catalog's owner** (`catalog.go`): `catalog_state` per agent; a
  disabled agent's queued runs stop and nothing new starts (the control
  plane refuses, and the runner re-checks at claim).
- **Samples** are the runner's own: every released version's self-test,
  once, under an install nobody owns (`status = 'catalog'`); the catalog and
  the data sheet read them.
- **Sign-off** is a job (`kind = 'review'`) like a re-render: the runner's
  current engine stamps the parent run's newest files (op `stamp_review`) and
  publishes them as the next version of each; no step of the run is re-run.
- **Every error** a person can reach comes from one table (`messages.go`):
  what happened, what to do next, whether their data is safe.

## One surface, and what a person reads (AM5)

- **The workspace is the chat's surface.** The composer, the message bubbles,
  the file cards, the failed-turn card, the working indicator, the question
  card and the model menu are the chat's components
  (`components/chat/ComposerFrame.tsx`, `MessageParts.tsx`,
  `AttachmentCard.tsx`, `ModelSelector.tsx`, `QuestionCard.tsx`,
  `CatIndicator.tsx`), configured. What only an agent has — round dividers,
  the files card, the list of documents not included, the run link — is an
  addition beside them. The workspace has no navigation of its own: the app's
  sidebar is the one list of agents.
- **No error text reaches a person.** What a failed round says is built from
  its reason's code and facts about the round (`failure.go` `FailureFor` — it
  takes no error); the error itself is kept on the run's steps for the run page
  and the problem report. Whatever is written to or read from the thread passes
  `GuardItem`; the web applies the same rule once more (`personText`).
- **A document left out is on a list.** `computed.json` `not_included`: why,
  and what bringing it back takes; `POST /runs/{id}/restore` applies it and the
  same round computes again.

## Matching (E3) and the completion gate (AM5)

- **E3** (`backend/agent_market/engine/e3/`): two tables of rows in, four
  groups out (matched · missing · extra · suspect), every row with its file,
  sheet, row and cells. Ordered passes in three tiers — `exact`,
  `amount_date`, `fuzzy`. Only a pass of the exact tier accepts a pair by
  itself (`Pass` refuses `auto` for any other tier); every other pair is a
  proposal a person confirms through the clarification engine, and records its
  pass, tier and who confirmed it. Tolerances are install settings
  (`match_*`, validated in `api.go` with the engine's own bounds). No model, no
  clock, no set order: the same input and answers give the same bytes.
- **A package's completion gate.** A manifest with `"completion_gate": true`
  puts its verdict in `computed.json` (`gate: {passed, code, what, next}`). The
  runner reads it after the files are made (`runner/gate.go`, a step of the
  run). Not passed: the files are delivered as a draft, the run ends `failed`
  with the gate's code, and the person reads the package's own sentence. The
  bank reconciliation's gate is `adjusted bank balance = adjusted book balance`.
- **Separate reviewer.** A manifest with `"review": {"separate_reviewer": true}`
  is signed by someone who did not prepare the run (`review.go`): who a person
  is is who is signed in, never the name typed on the form.
