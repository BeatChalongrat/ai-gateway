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
  gate (`quality.py`, using `doc_validator.py` and `pptx_inspector.py`) opens
  each finished file again (AM8) — see "The gate opens the finished file"
  below for what it asserts and what it does not.
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
`self-test.json`, `evals/` (`cases.json` ships in the runtime zip; `evals/fixtures/`
does not — see `runner/engine.go` `BuildZip` for every directory it leaves out).
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
  clock, no set order: the same input and answers give the same figures, the same
  pairs and the same questions (AM8: what `scripts/probes/AM6/same_input_twice.sh` compares —
  computed.json, the matching results, the cards asked and every cell's value; not the Office
  files' bytes, whose packaging carries times and metadata).
- **A package's completion gate.** A manifest with `"completion_gate": true`
  puts its verdict in `computed.json` (`gate: {passed, code, what, next}`). The
  runner reads it after the files are made (`runner/gate.go`, a step of the
  run). Not passed: the files are delivered as a draft, the run ends `failed`
  with the gate's code, and the person reads the package's own sentence. The
  bank reconciliation's gate is `adjusted bank balance = adjusted book balance`.
- **Separate reviewer.** A manifest with `"review": {"separate_reviewer": true}`
  is signed by someone who did not prepare the run (`review.go`): who a person
  is is who is signed in, never the name typed on the form.

## Invoice ↔ PO matching, charging, and what reaches a person (AM6)

- **The invoice ↔ PO agent** (`packages/po_matching`, `sran.invoice-po-matching`)
  matches at line level, never header level, and one E3 run per document links
  each invoice line to a PO line (custom passes: PO line number, item code,
  description by skeleton, then amount/date and fuzzy tiers a person confirms).
  The depth is decided per line from the files: three-way where a receipt line
  exists, two-way otherwise, and every line, the batch summary and the
  workbook's face say which one was used and why. Quantities are compared
  cumulatively per PO line (billed so far ≤ received so far ≤ ordered +
  allowance). Duplicates are searched across the whole document index
  (`detail` per entry) including a fuzzy pass, and are never counted. Three
  outcomes per invoice — ผ่าน · ติดเงื่อนไข (with a reason code) · ต้องให้คนตัดสิน;
  the agent marks nothing approved for payment, the best it says is
  "พร้อมเสนออนุมัติ". GR/IR is reported, never posted. Tolerances and the
  install's receipt practice (`ap_gr_practice`) are declared settings
  (`install.settings`), recorded on every line they decided.
- **Declared settings, scope, jurisdiction.** A manifest declares its settings
  (`install.settings`: key, kind, bounds, label) and says `scope`
  (`generic` | `org-specific`) and `jurisdiction`. The control plane validates
  them on registration and serves `declared` with an install's defaults.
- **Generic by default.** `scripts/agent-market/generic-check.sh` opens every
  file of a package (workbooks and documents unzipped, PDF text and metadata
  read), runs its self-test and looks into what it delivered, and walks the rest
  of the runtime zip (the engine) the same way; any name, id, tax id, host or
  mail of this repository's owner fails it. The publisher's namespace in a
  package id is reported apart. `scripts/probes/AM6/fresh_install.js` installs
  every agent on a test stack whose `agent_market` schema was just created and
  downloads what each self-test delivered for the same check.
- **Bank statements as PDF** go through E2's words: `engine/e2/grid.py` rebuilds
  a statement's table from word positions (amount columns by right edges, text
  columns by left edges, repeated headers dropped). A page without a text layer
  is not read — it becomes a question, never a guess.
- **Charge for work delivered.** A run's `outcome` is derived and never stored
  in `status`: `completed`, `completed_open` (the package's gate did not pass,
  its draft was delivered — "เสร็จแล้ว · ยังไม่ลงตัว"), `failed`, `cancelled`.
  `gate_open` is set by the runner when it ends a run on its gate; it is never
  worked out from a reason code. A run that delivered nothing has its ledger
  rows voided (`voided_at`, `void_reason`) and no budget counts them;
  `credits_cost` keeps what the platform paid. `GET /installs/{id}/estimate`
  says what a round costs before it starts.
- **Only text built for a person reaches a person.** The rule is positive
  (`persontext.go`, `reads.go`, `schema.go`): every thread item kind declares
  the fields a person reads and each is a `PersonText` built by the server or
  a package's declared summary key; anything else is withheld from the surface
  and kept for the run page.
- **The model picker is the chat's menu** (`components/chat/ModelSelector.tsx`)
  with nothing added: อัตโนมัติ is one row, a model the policy blocks cannot be
  chosen and says why once, and models that cannot do the job are one line at
  the end of their group. The workspace composer keeps attach, dictation, the
  model control and send; ฐานความรู้ is offered only in ask mode and is never the
  source of a number in a deliverable.

## The gate opens the finished file; the model proposes, the code disposes (AM8)

- **A gate reads the delivered file, never the writer's record.** Every
  figure check opens the file and reads the figure out of it — table cells,
  KPI tiles, chart series (pptx: the chart XML; docx: the chart is drawn again
  from computed.json and compared byte for byte, and its numbers are printed in
  a table under it that is read back), workbook cells — and compares it with
  computed.json. An empty cell where a figure belongs fails. The four files the
  output audit broke on purpose (`backend/agent_market/tests/fixtures/
  gate_mutations/`) must fail every run (`test_am8_gate.py`). Each check's
  `values` says how many figures it compared; the run's quality line says
  "files opened again · N figures matched", and the run-level checks
  (citations, assumptions) are asserted once, not per file.
- **A figure in a sentence is named by its key.** The narrative (model or
  rules) writes `{operating_expenses_var_budget_pct}`; the report prints the
  figure and *its own* citation there (`content.cite_runs`). The runner refuses
  a narrative that types a figure (`guardFigures`). Nothing is matched by
  printed text afterwards. Every P&L comparison (prior, budget, their
  differences) has its own citation, budget figures cite their cells, and a
  chart cites every point it draws. The appendix lists every source range.
- **Working papers print** (`deliverables/workpaper.py`): page setup from the
  sheet's shape (orientation, fit to width when still readable, otherwise the
  identifying columns repeat; header row repeats; the face on one page; print
  area; the document's name and "หน้า x / y" on every page), asserted on the
  file by `workpaper.check`. A header field (company — the install's, else the
  organisation's name — account, preparer) holds a real value or the paper
  says on its face that it is a draft for want of it; no placeholder.
- **Confidence tells the truth.** An invoice row whose text a column did not
  take lowers that item's confidence and becomes a card (`e2_item_text`);
  "ยืนยันแล้ว" is a person's word only — the machine says "ระบบอ่าน".
- **A customer's form: the model maps, the code checks.** The runner asks a
  model (one the organisation lets see documents) which slot is which field,
  from each slot's label and context; the person confirms; code proposes only
  identity. After filling, `template_out.verify` opens the file: every mapped
  slot holds its value, no `{{marker}}` is left, no filled text is cut off, no
  Word/PowerPoint chart still shows old numbers. A form that does not pass is
  delivered beside the agent's files, marked, with every unfilled slot named
  on the card; what the filler cannot do is refused before any work.

## Reminders, notifications and e-mail (AM6)

- **Nothing runs without a person.** A reminder is a schedule on an install
  (`reminders.go`): when it falls due, the runner's minute pass
  (`runner/reminders.go`) opens a round that *waits for files* — status
  `awaiting_input`, a `request_files` card listing the agent's inputs — and
  writes a notification. No step, no engine, no model, no charge: the round
  starts only when a person sends its files on that card, and it is theirs.
  There is no agent principal and no unattended execution.
- **At most one waiting round per reminder.** The next firing closes a round
  that still has no file (charged nothing) and opens the next period's. One
  follow-up three days later, then silence. Snooze moves the firing (or the
  follow-up); skip leaves a period out; stop fires nothing more. A round parked
  on a question for over four hours, whose install nobody opened since, gets
  one notification and one follow-up the same way. Every write carries a dedupe
  key and the pass takes rows `FOR UPDATE SKIP LOCKED`: a restart or a second
  runner never doubles a round or a notification. A reminder of an agent the
  organisation disabled opens nothing until the agent is back.
- **Notifications** (`notify.go`) are rows the clients poll: the web shows them
  in the chat's banner strip (and as a browser notification when the person
  allowed it); the desktop app polls them while it runs, tray included, and
  shows a Windows notification whose click opens a page of the app's own
  origin (`apps/desktop/src/main/agent-notify.ts`). Nothing is e-mailed.
- **Which surface a notification reaches (AM9, decided): every surface the
  person has.** A notification is listed for each of the person's clients —
  every browser, every device, the desktop app and the desktop app's own page —
  until somebody acts on it. Acting on it anywhere (open, snooze, skip, stop,
  close) dismisses it, which ends it on every surface; a page drops what was
  acted on elsewhere at its next read (30 s, or when it regains focus).
  `shown_web_at` / `shown_desktop_at` record when it was first shown on each
  kind of surface; they are a record, not a filter. Before AM9 they were the
  filter: the first page to show a notification stamped "web" for all of them,
  and since the desktop app's window loads the web page, a person who had the
  desktop app open never saw a reminder in their browser. Each client keeps
  what it has raised itself so nothing is raised twice: a browser's own
  notification once per browser (local storage), a Windows notification once
  per installation of the desktop app (a file in its user data). Tested with
  two pages of one person, and with the desktop app restarted.
- **E-mail** (`email.go`): a delivered round (completed or finished open) can
  be drafted as a mail — subject, a short Thai summary with its figures, the
  round's own files. The draft never has a recipient, and the text taken from
  a round passes a sanitiser that removes addresses, links and `mailto:`, so a
  document cannot choose where the mail goes, what it links to or what it
  attaches. Sending takes a configured `MailConnector`, the person typing the
  recipients and approving that exact message (`body_sha256`); every send is
  logged: who approved it, to whom, which files. No connector is configured in
  this product: the draft is copied and sent by the person.
- **Two transports, one approval (AM7, AM9).** The organisation's SMTP relay
  (`mail.go`) is the system writing to a person — reminders, from the
  organisation's address. A person sending their own work sends it from their
  own mailbox: chat-edge calls the MCP gateway's `mail` server
  (`services/mcp-gateway/servers/mail-send.js`) with the person's portal token
  and chat-edge's internal token (`personmail.go`); the gateway fetches that
  person's mailbox token from the backend broker into the child's environment
  only, and the child sends through the Gmail API with scope `gmail.send` (it
  cannot read mail), attachments as the round's own file bytes. The mail server
  is no model's tool: it is not in the catalogue models' tools come from, the
  gateway refuses it without chat-edge's token, the desktop never offers it,
  and an agent's model request has no tools at all. A connected mailbox is
  used first, then the relay on the person's behalf, then copy; the log
  (`email_sends.transport`) records which carried each send. With no Google
  client configured (the state a new customer starts in), no mailbox is
  offered and everything behaves as before.
