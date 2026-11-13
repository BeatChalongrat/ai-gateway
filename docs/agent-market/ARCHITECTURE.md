# Agent Market — architecture (AM1)

**Current entry point, 2026-11-12:** this cumulative document starts with AM1
and records later extensions below. The checkout now contains six packages:
financial_report, bank_reconciliation, invoice_processing, expense_audit,
po_matching and procurement. Ordinary Web Chat/Desktop Office tools have a
different execution path; see [Office workflows](../OFFICE_WORKFLOWS.md).
Generic chat now has its own optional Office converter wiring and overlay;
Agent Market's converter alone does not activate it. See
[Office templates](../OFFICE_TEMPLATES.md) for that separate delivery gate.

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
  pairs and the same questions (AM8: what the same-input-twice check compares —
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
  package id is reported apart. AM6's fresh-install check installed every agent
  on a test stack whose `agent_market` schema was just created and put what
  each self-test delivered through the same check.
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

## The details, on every surface (AM10)

- **The icon ships inside the package.** A package declares, in `agent.json`:

  ```json
  "icon": "chart",
  "icon_file": "icon.svg",
  "accent": {"light": "#1D4ED8", "dark": "#8FB0FF"}
  ```

  - `icon_file` — a file of the package (a relative path ending `.svg`). It is a
    *shape*, not a picture: one `<svg viewBox="0 0 24 24">` of strokes
    (`path`, `circle`, `ellipse`, `rect`, `line`, `polyline`, `polygon`, `g`,
    `title`), UTF-8, at most 4,096 bytes. The set's language is
    `fill="none"`, `stroke-width="1.75"`, round caps and joins; a third
    party's icon reads as part of the set when it follows it. Nothing a
    browser could run or fetch is accepted: no script, style, text, image,
    link, `use`, `foreignObject`, no event, `href`, `style` or `url(…)`
    attribute, no entity or directive (`look.go` `CheckIcon`; a package whose
    icon is missing or refused is not loaded at all).
  - `accent` — the agent's colour, one `#RRGGBB` per theme. The clients never
    draw the file's own colours: they paint the shape with the accent of the
    theme in use on a 14 % tint of it (a CSS mask), so one line icon reads the
    same at 20 px in the sidebar and at 48 px on its page, light and dark.
    Pick each so it stands 3:1 against the surface it is drawn on (white; the
    dark surface `#17181C`) — the shipped set is tested for it, and for no two
    agents sharing an icon or a colour.
  - `icon` — a glyph name, the fallback for an agent that ships no file.

  The runner reads the file at registration and writes it into the released
  version's manifest as `icon_svg` (a package never sets that key itself), so
  the catalogue never has to know an agent to draw it. The rail, the installs
  list and the workspace header take the newest released version's look for
  the agent, so an install pinned to a version from before the icon shows it
  too — presentation, not behaviour. AM10 rendered the set together at 32 px and
  at full size, light and dark, to check it.

- **A catalogue card says what it does, what to feed it and what comes back —
  nothing else.** The sample's price and run time, the tags and the scope line
  left the card (cost is the admin's, on the spend page; whom an agent is for
  is on its page and its data sheet). The time is said where the waiting
  happens: the progress card of a running round reads "ใช้เวลาประมาณ …" (the
  organisation's own rounds once there are three, else the catalogue sample),
  and after one and a half times that, "นานกว่าปกติ … แต่ยังทำงานอยู่".

- **Chats under each agent** (`chats.go`, migration 008). The sidebar opens
  each installed agent to its chats — pinned first, then the newest activity —
  each named for what it is (the period of its latest round; before any round,
  when it began; two of one period are told apart by when they began), with
  rename, pin, delete and "แชทใหม่กับ agent นี้". This supersedes AM4's "one
  workspace, rounds collapsed" where the two disagree. **Deleting a chat takes
  it off the list and nothing else**: a date on the chat, never a DELETE. Its
  rounds, their files, the run pages and the audit rows live on and are
  reached from the agent's overview; a round still queued or waiting in it is
  cancelled and charged nothing; the chat answers `THREAD_GONE` from then on
  (the workspace opens the agent's latest chat instead, a notification's round
  its run page), and a reminder never reopens a deleted first chat. The first
  chat has no row (`thread_id` NULL, "main"), so its name, pin and deletion
  live on the install. Two rounds side by side: `?beside=<chat>` reads a second
  chat of the agent beside the one on screen — its own live thread, read-only,
  one composer — so neither loses its place.

- **A form for this round only** (`roundform.go`, `runner/roundform.go`). A
  customer's form dropped into a round ("ใส่ในฟอร์มนี้ให้หน่อย") is used for
  that round and never becomes the install's: the round carries it
  (`agent_runs.round_template`), its file is the round's form and never one of
  its inputs (file status `form`), and the thread shows one item that follows
  it. Which file is a form: one the person ticked in the composer; a form's
  kind (.docx .xlsx .xlsm .pptx) the agent does not read as input; or, when
  the words ask for a form, every file of a form's kind. It goes to the round
  the message lands in — the one just finished, or the one running or waiting
  (filled when it finishes); with no round to put it in, the agent says so.
  AM8's rules hold exactly: the model proposes the mapping, the code checks,
  fills and re-reads, and a partly filled form is delivered as a draft with
  every slot it lacks named — never a pass. Every fillable slot proposed and
  accepted: filled without a question; otherwise the install's mapping screen
  opens on the form's item and saves to the round (`PUT /runs/{id}/template`).
  The fill is a render job that asks for no format, and a render asked for no
  format makes none of the agent's own files (`deliverables/formats.py`).

- **Which files, asked once** (`outputset.go`). An agent with more than one
  sensible set of files asks "ชุดไฟล์" at the postcompute moment — about to
  produce them — through the same card as every other clarification:
  cosmetic (the round runs on with the standard set; an answer re-renders),
  rememberable. It is asked only when the install does not already say: the
  wizard's formats step, the settings page and an earlier answer all count;
  a set copied from the manifest by a one-click install does not. The answer
  becomes the run's set and the install's `formats` setting. An answer never
  runs the round again: given after the files were delivered, the round stays
  finished and one render job makes the files it lacks (none when it has them
  all); given while the files were being made — after the render step read the
  answers — the same job is queued when the round finishes
  (`answersLeftBehind`). Any other answer on a card that does not make the
  round wait follows the same two paths and makes the round's files again.

- **One page across** (`workpaper.py`, `xlsx.print.one_across`). No sheet of
  any working paper runs across pages any more. A table too wide for one page
  is written as a main sheet — the row's identity, its amounts, its status and
  reason — and companion sheets right after it ("… · หลักฐาน", "… · เกณฑ์ที่ใช้",
  "… · ผลตามกฎ") that start with the same identity columns, one row per row,
  read side by side by row number. Nothing is dropped: every column is on
  exactly one of them (identity on all), and each check reads a column from
  the sheet that holds it. The gate fails a sheet that runs across more than
  one page by name, and a companion whose identity does not match its main
  sheet row for row.

- **Language.** Superseded by AM11 (below): the surface follows the app's
  language. What still holds from here: every agent follows Thai law and
  practice (`jurisdiction: TH`) whatever language it is read in, and the
  install setting `language` accepts only "th" and decides nothing.

## Two languages (AM11)

Vocabulary is settled in [GLOSSARY.md](GLOSSARY.md); every string of every
layer uses its terms.

- **The rule.** A string exists in Thai and in English or it does not exist.
  Each layer has a check that fails on one that is in one language only:
  the web's dictionaries are typed against each other (a missing key is a
  compile error in either direction) and `npm run lint:i18n` compares them
  before its ratchet; the server's test reads the Go source of both planes and
  fails on a Thai sentence with no English, on an English entry nothing uses,
  and on a message code in one map only (`am11_language_test.go`).
- **The screen** (`features/agent-market/i18n*.ts`): each dictionary file has
  its English beside its Thai; the lookup and the locale for dates follow the
  app's language, which is the person's account's (AM10).
- **What the server says.** A sentence for a person is written in the source
  in Thai; its English is in the catalogue (`english*.go`), found by the Thai
  sentence itself, so the place that builds a sentence does not change when a
  language is added. `Text(...)` renders both and a `PersonText` carries both;
  which one a person gets is decided where the text is written or sent
  (`lang.go`: `MarshalIn`), and Thai is what a writer that was never taught
  says — a missed place shows Thai, as before, never half a sentence. The
  language of one `MarshalIn` is that call's only: it is kept under the
  goroutine that is marshalling, so a plain `json.Marshal` running at the same
  moment for another person still writes Thai (`am11_marshal_test.go`; the
  first shape, one shared variable, let 12,772 of 32,000 such writes come out
  English). An error's message has its own two maps (`messages.go`,
  `messages_en.go`); a reminder's mail has its own (`reminders_en.go`).
- **What a person types** is read by its words in both languages (`words.go`):
  a Thai word is found anywhere, a word in Latin letters only as a whole word
  ("excel" is not in "excellent", "word" not in "in other words").
- **Which language.** A request names its screen's language (`Accept-Language`
  with exactly one tag, or `?lang=`; anything else is Thai, which is what every
  client before AM11 sends). What the control plane answers — an error, an
  estimate, the data sheet — is in it. **A run has one language**: the one of
  the screen that started it (`agent_runs.params.lang`; none = Thai). Every job
  done for a run is in the run's language whatever screen asked for it, the
  runner writes everything for a run in it, and an engine step is told it
  (`"lang"` in the step's request). A finished run stays as it was written.
- **What a person may read, in English.** In Thai, "text written for a person"
  is decided by the script (`reads.go`). In English it is decided by
  vocabulary: a word is readable when one of the product's own English
  sentences (or a package's own English) uses it. This is weaker than the Thai
  rule — an error can be made of ordinary English words — so the list of error
  shapes is asked as before, no English sentence of ours may read like one,
  and a line with no Thai word that has an error's shape is not shown at all
  (the Thai reader shows nothing of "engine step failed: exit status 2"; the
  English one was showing "… step failed: … status 2"). A thread item says
  which language it was written in (`"lang"`): a Thai item is read by the
  strict Thai rule exactly as before AM11.
- **Numbers, dates and money** follow the language where they are formatting:
  `28 ก.ย. 2569` / `28 Sep 2026`, `กันยายน 2569` / `September 2026`, the
  Buddhist era on a Thai screen only. The values never change, and no rule of
  law is a format.
- **A document is in its package's language, not the run's.** A package says
  which languages it speaks — what it asks, the labels of its figures, the
  documents it makes (manifest `languages`; none said = Thai only;
  `Manifest.Speaks`). Whatever the platform adds to a document follows the
  package (`runCtx.docLang`): the commentary a model writes for a report, the
  preparer's name on a sample, which text check a PDF gets (`pdf.thai` for a
  Thai report, `pdf.text` for one written in English). A document is never
  half one language and half the other. What is said *in the thread* follows
  the run: the platform's sentences, and a model's answer to a question a
  person typed there.
- **What a package's manifest says is the package's, in both languages.** What
  a person reads before a run exists — what the agent is, what it needs and
  gives back, what it cannot do, its settings and their help, its guide — is
  in the manifest, and its English is there too, under `"en"`: a flat map from
  each Thai string, exactly as the manifest has it, to its English (261 strings
  in the five packages). The server sends a manifest in the screen's language
  (`english_package.go`: `ManifestIn`; `"en"` itself never leaves the server),
  names an agent by `name_en` on an English screen, builds the data sheet from
  it, and reads a package's string that it puts into a card from the same map.
  Three things in a manifest are not words and are never translated: a sample
  file's path; the values an install starts with (`install.defaults` — the
  expense policy's categories and prohibited merchants become the customer's
  own on the first save and are matched against the customer's Thai claims);
  and a literal a person must find or type as their files have it (the sheet
  the bank agent reads is “รายการค้างยกไป” in English too, with what it means
  beside it). `test_manifest_english.py` (Python lane) and
  `am11_packages_test.go` fail a Thai string with no English, an entry left
  over, Thai in an English entry, a placeholder or a number that differs, and
  an English for anything that is data.
- **A code is the engine's name for a thing, never what a page shows.** A
  setting is named in the reader's language whoever saved it (its own name is
  not the person's data; a name somebody gave is left alone). The dashboard's
  rows carry their words from the server — a question by the heading its card
  last carried, a failed run by the sentence a person is told for that reason —
  and the web has a sentence for a code it does not know ("A question the agent
  asks", "A step of the agent"), never the code.
- **What is not in English yet.** The words *inside a generated document* —
  a sheet's name, a heading, a label in a file. No package lists English in
  `languages`, a document is written in Thai whatever the run's language, and
  an agent's page says so before a person installs it. Everything a package
  says *to the person* while it runs — a question and its answers, a check's
  line, why a run ended as it did, an exception, what was assumed — has been
  in the run's language since AM14 (below: "An engine in two languages").

## A form with no marker in it (AM11)

AM10 filled a form that carried `{{markers}}`. A customer's form usually
carries nothing; what was true of it before AM11, found by reading and by
running four forms of four shapes (`tests/formkit.py`):

- it was read only as "a label with an empty cell to its right";
- a form with one field of six filled came back **passed**, "every slot has a
  value": only the mapped slots were re-read;
- there was no refusal: a purchase request given to the report agent went to
  the mapping screen.

Now (`deliverables/template_fill.py`, `template_out.py`, `template.go`,
`runner/roundform.go`):

- **What is a blank.** A cell beside its label, or under its heading where the
  cell beside the heading holds something (a row of headings over a row of
  blanks), in a Word table and on a sheet. Each blank says where its label is
  and whether the form **expects it filled**: a label ending in a colon, a cell
  drawn as a blank (a border, a fill), one of a column of labels, any such cell
  of a Word form's table. A title or a note with an empty cell next to it is
  offered on the mapping screen and is not a gap.
- **The gap list is the file's, not the mapping's.** The filled file is opened
  again: every mapped field holds its value, each value sits beside the label
  it was mapped under (`template.beside_label`), and every blank the form
  expects filled holds something — mapped or not. What does not is named with
  its reason, and the form is a draft. The person reads the list under its own
  heading: ช่องที่ยังกรอกไม่ได้ / Fields that could not be filled.
- **Refused before any work.** When a model has read the form's labels and the
  agent has a value for less than half of the blanks the form expects filled
  (three or more), or nothing in the file takes a value, nothing is made: the
  form's item says how many fields there are, how many have a value, names the
  rest and offers the mapping screen. A person's own mapping that covers enough
  makes the form ready; a save that covers less than half is refused with the
  names. With no model to read the form, nothing is known about what the agent
  could fill, and the person maps it.
- **The same form again.** A form dropped into the same run a second time is
  read and filled a second time: the jobs that read and fill it are one per
  upload. (Keyed by the file's content, as they were, the second drop found
  the first drop's finished job and its card stayed at "reading the form".)
- **What is still not read as a blank** — and is therefore neither filled nor
  named: a line of underscores or dots after a label in running text
  ("ผู้จัดทำ: ______"), a signature line, a blank under a heading on a sheet
  when it is not drawn as one, and the rows of a table under its first empty
  row (line items: a list goes only to a marked repeating row or a named
  range). A form whose fields are of these shapes reads as having few fields
  or none.

## A round on the web (AM12)

The procurement agent (`sran.procurement`) is the first agent that touches
the network. A person says what to buy; the agent finds sellers' pages,
reads them, and gives back a comparison and a draft purchase request. The
model decides where to look. What makes that safe is not a list of allowed
sites — there is none — but that **nothing a page or a model says is taken
on its word**: every value in the table is found again, by code, in a stored
copy of the page.

**Who may reach what.**

| Part | Reaches the web? | How |
|---|---|---|
| agent-runner | no | asks `web-fetcher` for one address at a time (network `fetch`, internal); searches through the model gateway's own `/v1/search` with its existing key |
| the sandbox (the package's code) | no | `--network none`, as every step; it is handed files |
| `web-fetcher` | yes — it is the only one | network `web`; no key, no volume, no docker socket, not on the core network |
| the models | no tools | a prompt and a typed JSON answer; the request type has no field a tool could be offered in |

**A prompt is two parts.** Each of a package's prompts (`web.prompts` in its
manifest) is one file: the rules, a line `=== with the question ===`, and
under it the shape of the answer. The runner sends the rules as the system
message and the second part at the end of the user message, after the data
(`runner/web.go: splitPrompt`). The shape must never be among the rules: the
platform removes from every model answer any run of 40 characters that repeats
the system message (`backend/app/services/prompt_echo_guard.py`), and an
answer that follows a shape printed there comes back with pieces of its own
JSON taken out — the page is then "read by no model".
`backend/tests/test_am12_prompt_echo.py` puts every package prompt through
that guard; `runner/am12_prompt_test.go` holds the file format.

**The door.** An agent whose manifest has a `web` block is off until an
admin turns it on for that agent (the per-agent switch of AM9; its default
for such an agent is off, whatever its category). While it is off the agent
installs and opens, the workspace says it cannot search yet and who can turn
it on, and a request sent anyway is answered in the thread with the same
reason: no run is created (`web.go: webClosed`). The runner checks again
(`runner/web.go: webBrief`): with the switch off nothing of the request
leaves, whatever started the run. `AGENT_MARKET_ENABLED=false` removes all of
it, and the fetcher answers every request with 503.

**The steps** (each an idempotent step like any other; a run that is resumed
after a question reads them back and repeats none):

1. `web.brief` — a model reads the person's words into a brief (items,
   quantity, the requirements the person named, where, by when). The package
   settles it (`checks/need.py`): anything that is not the shape asked for is
   dropped; a quantity is a number the person gave, never assumed.
2. clarification — only what a search cannot start without (which of several
   things is meant, how many; the delivery address once). At most four
   questions on one card.
3. `web.plan` → `web.search:n` — a model writes queries; each goes to the
   organisation's search provider. Capped per run.
4. `web.select` — a model picks which results to open, **by their number in
   the list**. An address a model writes itself opens nothing; a number that
   is not in the list opens nothing. Links the person typed are in the list.
5. `web.fetch:sNN` — the fetcher opens one address (below). Its answer is the
   evidence package or a refusal with its reason. A host on the admin's
   never-open list is not asked for at all.
6. `web.read:sNN` — a model is shown the page's **visible** text as numbered
   blocks and returns claims: for each thing it says the page shows, the
   words as printed and the block they are printed in.
7. compute (`scripts/compare.py`) — every claim is looked for in the stored
   snapshot (below). Amounts are arithmetic in code on what was found.
8. the choice — one card, kind `pick`: the comparison and "which candidate,
   or none". Nothing is marked as the one to take.
9. render — the comparison workbook, and the purchase request: in the
   organisation's own form when one is set or was sent, else as a plain
   document. Both name, on their face, what could not be filled and why.

**The fetcher** (`services/web-fetcher`). One address in; out, what a person
would have seen (text as blocks with where each sits and the box it is drawn
in, a picture of the page, the markup made inert, the response's headers,
hashes, a log of the fetch) or a refusal. A fresh browser profile each time;
no cookie or cache is kept; nothing stays in the service after it answers.
It refuses — each with its own reason, shown to the person in words:

- an address that is not `http`/`https` on the standard port, or carries a
  user name; a host that does not resolve; any host of which **any** address
  is private, loopback, link-local (the cloud metadata address), carrier
  NAT or otherwise not public; a name of the platform's own services;
- every host but the one named in the request: the browser's only way out is
  a per-fetch proxy that connects to that host at the address that was
  vetted, and to nothing else. A redirect to another host, a page that
  navigates itself to one, a frame, a script or an image from one, is not
  followed and not loaded (recorded as not opened);
- a page the site's `robots.txt` disallows for it, or whose own markup says
  not to use it for automated systems (read at the time of the fetch — the
  rule is the site's, applied when its page is opened, and shown with the
  refusal); an unreadable `robots.txt` is a refusal, not a pass;
- a sign-in or payment page, a paywall, a challenge to prove being a person
  (never worked around: the source is dropped and the table says so), a
  download that is not a page, a response over the size limit, a page that
  shows nothing when only its own host is loaded.

Text a person could not see — not rendered, hidden by style, transparent,
too small, off the page, cut away, the colour of its background, covered —
and everything a page carries besides its text (meta tags, JSON-LD,
comments, `alt`/`title`/`aria-label`) is stored apart from the visible text
and is **never shown to a model or checked against**.

**Finding a value again** (`engine/e4`). A claim is `{quote, block}`. It is
looked for in the block named: exactly, then with spacing made uniform, then
with character forms made uniform (full-width and Thai digits, the marks
that print as a space or a hyphen). A figure is found whole, never inside a
longer figure. A quote that is in one block only is that block; in several
and not the one named, it is not found. There is no pattern library and no
price parser: the model gives the string as displayed and the amount it
reads it as; code works out what that string can mean from its own
characters (`money.readings`) and takes the amount only if it is one of
them, in the currency whose mark is printed **with the figure**.

A figure is a price only when, besides that: it is not struck through; it
is shown as a figure (little else in its block, no second figure); and it is
printed with the product it was read for — in the same part of the page as
the product's name, under no other heading, with no other figure shown as a
price nearer to that name (a page lists several products and several
prices). Each refusal has a code and a sentence; a figure that is not taken
is "not known", is counted as a point still open, and reaches the draft only
as a named gap. What code cannot tell from where a figure sits — a delivery
fee printed alone beside the product — is the reader's to tell, and the
picture cut from the page shows the person what was read.

What a reading cannot do, by the shape of what is kept from it: it has no
field that makes a seller approved or on the organisation's list (that is
worked out from the organisation's own list — by site, tax ID or name); a
tax ID is a number of the jurisdiction's length or it is not one; tax
treatment, delivery, lead time, minimum order, stock and warranty are each
a quote found on the page or unknown; nothing it adds is carried anywhere.
The permanent proof is the injection corpus
(`tests/test_am12_web_evidence.py` on pages captured by the real fetcher,
`runner/am12_it_test.go`): a reading that has already been talked into the
wrong answer changes nothing.

**Keeping.** A saved page of a source that became a candidate is evidence of
its run and is kept as long as the run. One that did not is deleted after
the admin's number of days (7 unless set); a page that could not be opened
is not saved. The line that an address was asked for, and what came of it,
stays (`web_calls`) — it is what the admin's Web tab and the run page's
"pages opened" list are read from. There is no crawl cache: the same address
in another run is fetched again.

**The organisation's own form** (`deliverables/form_regions.py`,
`pdf_form.py`; see "A form with no marker in it"). The procurement manifest
asks for three more readings of an unmarked form (`templates.reads`; an
agent that does not ask reads forms exactly as before): the rows of a table
of items — a row of headings over empty rows is one place that takes a list,
its columns mapped by their headings; a blank drawn as a line after its
label; and a PDF with fields to type in, each read under the words printed
beside it. Word gains rows when there are more lines than rows; Excel never
does (its formulas would not move) and says which lines did not fit. The
filled file is opened again: each value beside the label it was mapped
under, each line in its row; and the gaps — the form's own blanks still
empty, read from the file, and what no web page can tell — are printed at
its end and looked for there.

**Words.** Every sentence the package writes is in its manifest in both
languages (`strings` by key, `en` by the Thai); its code holds keys only
(`tests/test_am12_package_words.py`). Nothing in its code, prompts or
manifest names a kind of thing to buy.

## The agent's room, and an agent that does what it is asked (AM11b)

Four things the earlier cards left unsaid, and one new mechanism.

**No money on a person's screen.** A person using an agent reads the time a
run takes and nothing about what it costs: no credits, no baht — on the line
where a run starts, the files card, the run page, a budget notice, a failure,
a tooltip. The accounting is unchanged (a failed run is still free, a
finished one still charged; `budget.go`, `failure.go`); only the words left
the screens. The figures live in the admin app (Spend). `EstimateFor`
returns seconds only; `am11b_money_test.go` reads every sentence the server
can say to a person and fails on one that names credits.

**The picker lists what can run.** `MenuFor` lists a model only if this
agent may use it now: a model outside the organisation is absent while the
agent's data may not leave it (one line says so — `outside_absent`), a model
that cannot read images is absent from a vision agent, and a model whose
stated context window is under `MinAgentContext` (32,000 tokens) is absent
from every agent — a 16k in-house model was listed, picked, refused by the
gateway with `context_length_exceeded`, and another model wrote the report.
A stored choice that is no longer listed reads as "auto" on the screen and in
the runner (`runner/choice.go`), the same rule in both places. A listed model
that fails to answer on the day is still rescued by another (AM4), and the
files card says so in words.

**One empty chat.** "New chat with this agent" opens the unused chat that is
there instead of making another (`createThread` answers `reused`), listing
the chats soft-deletes every other chat nothing ever happened in
(`TidyEmptyThreads`: no round, no message, no file, not pinned, not named by
the person), and a chat is named for the first thing that happens in it —
the request, the period, the file, the words. A chat with a round is never
touched, nor anything of a round.

**Instructions.** A package may say in its manifest what a person can ask of
its documents, and what they are known to ask that it cannot do:

```json
"instructions": {
  "can":    [{"id": "no_charts", "label": "…", "note": "…", "unless": "…"}],
  "cannot": [{"id": "by_department", "label": "…", "why": "…"}]
}
```

The pieces, and who owns what:

| Part | Does | Never does |
|---|---|---|
| a model (`runner/asked.go`, step `instructions`) | reads the person's words into ids of the two lists (`AskedPrompt`) | writes a figure, a reason, or anything applied as written |
| `ReadAsked` (`instructions.go`) | keeps only declared ids, each once; drops a quote that is not the person's own words | trusts the answer |
| the package's renderer | applies a directive at render time — leaves a section out, orders a table's rows (`deliverables/content.py: asked_of`) | computes: the computed table and its citations are the same with or without it |
| the package's gate | opens the finished file and reads back that it was done (`docx.asked`, `xlsx.asked`); a file not made as asked fails | counts on a normal round — the check exists only when something was asked |
| the thread | says what was done (`AskedDone`: the directive's own `note`) and each request not done with its reason (`AskedNotDone`: "ทำตามที่ขอไม่ได้เพราะ…") | stays silent about a request it did not honour |

A reason is the package's (`why`; `unless` when this round's data cannot
take a directive — ordering by the difference with nothing to compare) or
the platform's (`NeverAsked`: changing a figure, skipping a check, another
tax rule — the same for every agent). The model is never shown a reason and
never writes one. It is one of the models the round may use (`writingModels`),
so an agent whose data may not leave the organisation has its person's words
read inside it. The question's system message holds no JSON (the echo guard,
AM12); the shape is asked for in the user turn.

Words typed with the files are read once, before the documents are made.
Words typed after a finished round, about its documents, start a render job
— the same computed table, no compute step — with the directives now in
force and the new ones (`InstructionsInForce`; `replyToAsked`), so a file
made later for that round (a PDF, another format) is made the same way. A
new round starts with none.

An install is pinned to its version: only an install of a version whose
manifest has `instructions` takes them, and only such an agent's greeting
says so (the package's own labels). An agent that declares nothing makes its
documents one way and does not claim to listen.

**The echo guard and a package's instructions.** AM12 found the platform's
prompt-echo guard cutting a JSON shape out of answers; checking the other
agents found it cutting the report agent's commentary too. A package's
`AGENT.md` is the system message of its commentary, and the report agent's
printed, as an example, a sentence a commentary really writes — the words
"…สูงกว่างบ" followed by a real figure's name. A model that said the same in
the same words lost those 44 characters: the executive summary then read
"…ค่าใช้จ่ายในการขายและบริหารที่:abs}" (4 of the 27 commentaries a model wrote
on live, 1–6 Oct 2026). Two things hold it now: a package's instructions
print no example sentence with a figure's name in it
(`backend/tests/test_am11b_instructions_echo.py`, with the guard itself, for
every package), and a commentary in which a brace is left over once the
figures' names are taken out is no answer (`runner/deliver.go: guardFigures`)
— the model is told and asked again, and a second cut ends in the rules' own
commentary. The second holds for every installed version; the first reaches
an install when it is upgraded to the version that carries it (report agent
1.8.0).

## An order for the table, on every agent (AM13)

AM11b made two agents do what they are asked. AM13 is the other four, and it
is one thing only: **the order of a table's rows.** No agent drops a row,
adds a subtotal, or says which row is best because it was asked to.

| Agent | What can be asked | What it is asked and does not do (with the reason) |
|---|---|---|
| Invoice ↔ PO matching 1.5.0 | lines from the largest price difference | only the lines that failed · approve payment |
| Bank reconciliation 1.6.0 | items from the largest amount · items by date | leave out small items · post the entries |
| Invoice processing 1.8.0 | register by seller · by document date · from the largest total | a sheet per seller · only the documents with a problem |
| Procurement 1.1.0 | options from the lowest price | say which option is best · leave out options with something unknown |

**Applied once, at the one list.** Each package's `_as_asked(computed, req)`
returns the computed table with that one list in the order asked
(`deliverables/asked.py`: largest first, smallest first, by name — all
stable, so rows of one size keep the order of the person's file; a row with
no value goes last). The writer and the gate are both handed that view, so
a wide table's companion sheets — evidence, criteria — are made from the
same rows and stay row for row. Everything else is made from `computed` as
before: the statement of a reconciliation and the list carried to next
month, the totals of a register, a comparison's request draft.

**Read back from the file.** A round that was asked something has one more
check, `xlsx.asked`: the finished workbook is opened and the order is read
from its own cells — the amounts, the dates as the paper prints them, the
sellers' names. A file left in the old order fails the round
(`test_am13_asked.py` is those refusal cases).

**A table has one order (the platform's rule).** Directives of one table's
order carry `"group": "order"` in the manifest. `ReadAsked` applies one of a
group — the first the package lists — and records the others in
`Asked.Instead`; the thread says of each “… ทำตามที่ขอไม่ได้เพราะขอ “…”
มาด้วย และทำได้ทีละอย่าง”. Asked for another order in a later message,
`InstructionSpec.Merge` drops what was in force in that group and puts the
new one in: a renderer is never handed two. A later message's thread speaks
only of what that message asked (an order that was replaced is not reported
as refused).

**Procurement: an order, never a verdict.** The owner's rule: sorting by
price is allowed, printing a "best" verdict is not, and the caveats stay on
every row.

- Like is compared with like. When every priced option states its tax, the
  order is by the total with tax; otherwise by the unit price each page
  shows, and the workbook's face says which and points at each row's tax
  column. Options with no price are last. Prices in two currencies are not
  put in order at all: the directive is refused with that reason
  (`unless`) and the table stays as it was.
- An option keeps its number (the person chooses by it), its count of
  points not known, and its rows on the sheet of what to ask the seller.
  `xlsx.asked` reads all three back, and that the face says the order is by
  price only and that the table is not advice.
- The only cell the ordered workbook has that the default one has not is
  that one sentence (`test_nothing_is_marked_as_best…` compares every cell).
- **Not ordered: the card a person chooses from in the thread** — by
  decision, not for want of a mechanism. The card is where a person decides
  and the Excel is where they analyse; a card in the order of price would
  read as "the cheapest is best". It stays in the computed order — fewest
  unknown points first — and says so where a person sees it: the card's own
  line (`cmp.order`) says it is not in the order of price and that the Excel
  file is when asked; the directive is named "… in the Excel file"; and what
  the thread says when the files arrive begins with it.

**A request is not a refused request.** Procurement starts from what a
person types, and the same words are read for what they ask of the
documents. "งบไม่เกิน 5,000" is the request; a model that answers it as
`other` is not believed: for the round's own message of an agent that
starts from a request, `Asked.OfARequest` drops every `other` before the
step is stored, so nothing of the request is ever answered with "cannot be
done as asked". The model is also told, in the user turn (the system
message is the same for every agent and holds no JSON). What the package
lists as not possible — a verdict — is still said, with its reason.

**What it costs.** For these four agents a round that has words with it now
makes one more model call (the reading: about 950 tokens). The three that
show no model their documents send those words — and only the words — to a
model inside the organisation, and their data sheets now say so. A round
with no words, or with only thanks, makes none.

## Where a run's data may go, and whether it is masked (AM16; AM13b before it)

Two switches of the admin's per agent (`egress.go`, table `agent_egress`), both off by default, each
change of either in the audit log: **documents may leave** (`documents_may_leave`, under the
organisation's own switch) and **masked** (`masked`). Until AM16 the second was not a switch: masking
followed the first (AM13b — "may leave" meant masked, "stays inside" meant as typed). The owner
separated them: reading a document correctly matters more than masking it on the way, so documents
that leave are sent as typed unless an admin turns masking on for that agent.

One rule, two parties. The **runner** knows what the two switches say for the run's agent
(`rc.docsMayLeave`, `rc.masked`, read on every pass — `choice.go switchesOf`) and which model it is
calling; the **gateway** owns masking. So the runner states the facts and the gateway decides:

- `runner/masking.go` — every model call a step makes carries both in its context (`rc.step` wraps the
  step's context; `askModel`, which answers outside a step, does the same). The model client
  (`model.go send`) sends `X-Sran-Masking: off | on`, and `X-Sran-Egress: closed` only when masking is
  off, the data may not leave AND the model is in-house — the one header an older gateway knows, sent
  only where it means what it always meant.
- The gateway answers each call with what it DID with the text (`X-Gateway-Masking: off | on`); the
  client notes that, not the request. `rc.step` writes it beside the step's own output (`"masking"` in
  `agent_run_steps.output`).
- **What left as typed is noted separately** (`sent_out_unmasked` beside the step's output): a call that
  went to a model outside the organisation with text the gateway did not mask, or with the image of a
  page. A page image is never masked — the gateway masks text — so it counts whatever the gateway said.
  A call that failed after the provider may have been sent it counts too (not one the gateway refused
  before sending: 401, 402, 403, 404, 422), and so does an answer given by another model than the one
  asked. It is written beside a failed step as well and kept when the step is run again.
- **A page stays inside while the agent is masked** (`choice.go mayRead`, `Egress.PagesMayLeave`): an
  image cannot be masked, so with masking on no outside model is given a page, and the model menu of an
  agent that reads pages has none (`models.go MenuFor`, with the reason).
- The cards: `Progress.SentOutUnmasked` — on the run's own card as soon as it has happened, whatever
  the run then comes to (`progress.go syncProgress`) — and `OutputsModel.SentOutUnmasked` beside
  `OutputsModel.Masking` on the card of the files (`thread.go`, declared in `schema.go`). Web: one
  sentence on the face of both cards (`workspace/SentOut.tsx`, strings in `i18n.am16.ts`), and the
  older line about the text in the "seen by" block. An answer to a question is not a step: when its
  question and figures left as typed, a warning notice says so in the thread (`SentOutAnswerNotice`).
- Admin app: two checkboxes per agent and what the two together mean now, in words
  (`admin-ui/app/agent-market/_sections/AgentEgress.tsx`).

One model call is made outside all of this and is therefore always masked and never counted: reading a
person's typed answer to a card into one of its options (`clarify.go interpretCards`, called with the
pipeline's own context). It sends no word about masking, and every gateway masks such a request.

What holds it in place: unit tests of what the gateway is told in every combination, of what is noted
as sent out (text, a page, a failed call, a refused one, another model's answer), and of the page rule;
integration tests that run one agent through all four combinations and against a gateway that does not
comply (`am13b_masking_it_test.go`), and a scan read by an outside model — said on the card, not read
at all once masking is on, and still said when the model never answered (`am16_sent_out_it_test.go`);
the gateway's own tests of the refusals (`backend/tests/test_dlp_in_house.py`); and a test that no
file of the runner calls the model client with a context of its own making.

**Why the switches were separated — the reason as it stands.** The change was asked for on the ground
that masking stopped the tax-invoice agent from reading a document. Measured before anything was
changed (12 Oct 2026, one scanned tax invoice, five ways of sending it), that was not so: both tax ids
came back right masked or not, because a scanned page is an image and no path of the gateway masks an
image. The owner corrected the reason in writing and kept the decision. What decides how a scanned page
is read is whether it may leave and which model reads it; the masking switch changes what happens to
typed text.

**A question says who read the page.** The engine's question beside a model's reading said "the
in-house model read … from the image", and its label for that reading "the in-house model reading the
image", whoever had read it — so a run whose page went to a model outside told its user two things at
once: the card that the page had been sent out, the question that an in-house model had read it. The
runner writes the model and whether it runs in the organisation beside every reading
(`runner/documents.go`, `proposals.json`); the three packages that read pages hand that to every
question they ask (`cards.reader_of`), and the sentence is chosen from it (`cards._reader`): in-house
only on the runner's word (the flag `true`, nothing that looks like it), a model outside named in
quotes — “google/gemini-2.5-flash” — and neither when nothing was said. A question's key does not move
with the reader. The same claim stood in the three agents' guides ("sent to a model that runs inside
the organisation"), in their `AGENT.md`, and in the catalogue's label on the web client ("run inside
the organisation only"); each now says what is true: the model chosen for the agent, normally in-house,
outside only where an admin allowed it, and the run's card says when. A released version keeps the
engine and the manifest it was registered with, so this reaches an install when it is upgraded to
tax-invoice 1.10.0, expense audit 1.9.0, invoice ↔ PO matching 1.7.0. Held by
`backend/agent_market/tests/test_am16_reader.py` (a real scan through the agent's steps, both
languages, six ways of breaking it shown red) and `am16_reader_test.go` (the same cards through the
thread's own door: the control plane reads a model's name inside its quotes and sends the sentence as
written).

## To whom an agent's documents may go (AM16)

"Documents may leave" said yes or no. It did not say to whom: with it on, every model of the catalogue
was open — and behind a router a model's name does not say who runs it (asked of OpenRouter on 13 Oct
2026 with this deployment's key: Claude was answered by Amazon Bedrock, GPT by Azure, DeepSeek's and
Meta's models by whoever hosted them that day; only Google's by Google). The owner: the admin chooses
which outside providers an agent may reach; both the route and the company are named, because both
read what is sent; "may leave" on with nothing ticked means nobody; the organisation has its own list,
the ceiling, and an agent's list may only narrow it; nothing goes to a provider that is not ticked.

**A recipient** is a key the gateway gives for each model of its catalogue (`GET /v1/models`,
`recipients`; `backend/app/services/providers/recipients.py`): `<route>` for a provider reached
directly, `<route>:<company>` behind a router — once for each company that can **run** the model
there. Nobody types a key. A model nobody can be said to run has none, and can never be allowed.

**Two lists** (`recipients.go`, migration `011_am16b`): `org_recipients`, the organisation's own, and
`agent_recipients`, one agent's. What counts for an agent (`Egress.Recipients`) is what both hold, and
only while its documents may leave at all — so the list opens nothing by itself, and neither does the
switch. The admin door (`GET /admin/egress`: `available`, `org_recipients`, each agent's
`agent_recipients` and `recipients`; `PUT /admin/egress/recipients`; `PUT /admin/egress/{slug}` with
`recipients`) refuses a recipient the catalogue does not name and a tick for an agent that the
organisation's list does not hold, and writes one audit line for each recipient added or removed
(`agent_market.org.recipient_allowed|removed`, `agent_market.agent.recipient_allowed|removed`, with
route and company). Taking a recipient off the organisation's list takes it from every agent at once;
the agent's own tick stays where it is and counts for nothing.

**Three places hold it**, the last being the one that counts:

- the menu (`models.go lock`) offers an outside model only when a company that runs it is named for the
  agent, and says why it is shorter (`reasonRecipient`);
- the runner (`runner/choice.go mayUse`, `mayRead`) uses an outside model only under the same rule;
- every model call of a round carries the agent's list (`runner/masking.go withRecipients`,
  `model.go`: `metadata.allowed_recipients` — nobody is the empty list, never nothing and never
  `null`), and the gateway refuses, before anything is sent, a call to anyone not on it: the first
  attempt, a second route, a rescue by another model. Behind OpenRouter it pins the request to the
  endpoints of the companies named, with no fallback to another host.

A call the runner makes outside any round (the check of its own in-house model) carries no list and
is unchanged. What the list does not cover: the search provider and the sites of an agent that works
on the web — those are opened by the leave switch as before (`web.go`).

Held by: `am16_recipients_test.go` (the two lists through the door, the audit lines, the menu, the
migration and its down), `runner/am16_recipients_test.go` (who may be used; that every call of a round
carries the list, said as an empty list when it is nobody), `runner/am16_sent_out_it_test.go` (a scan
stays inside until the company that runs the model is named for the agent — on the real engine), and
the gateway's own `backend/tests/test_recipients.py`.

## An engine in two languages (AM14)

Until AM14 a package's engine wrote Thai whatever the screen: its sentences were string literals in the
Python that computes — 4,954 of them in 67 files. An engine step was told the run's language and did
nothing with it. On an English screen a run read English around Thai, and the first thing a person saw —
the agent's greeting — was Thai too, for a different reason.

**What a row keeps in one language and a person reads in another** (`asread_am14.go`). Two things about an
installed agent were written once: its name (`agent_installs.title`, set from the screen it was installed
on, never updated — not even by an upgrade) and its greeting with the tick list (one thread item, in the
language of the request that made it). Neither is the work of a run. Both are now read from the agent's
own manifest when they are sent: the name from the newest registered version of the agent
(`AgentNameIn`; applied by hand wherever an install is written to a person — a JSON writer on the type
would be inherited by every response that embeds an install), the greeting's words from the version the
install is on, in the thread and in its live stream (`greetingsAsRead`). Nothing is rewritten.

**A sentence is not written in the code** (`backend/agent_market/engine/words.py`). It has a key; its Thai
is in the package's manifest under `strings` and its English under `en`, found by the Thai — the place
and the rule of what a manifest says before a run exists (AM11) and of every word of the procurement
package (AM12). The shared engine's own sentences (a matching card, a question about a document, what a
file read back showed) are in `engine/words.json`, by the same rule.

- `use(lang)` — the step says the run's language once (`engine/step.py`); one step of one run is one
  process. `W("key", place=value)` says a sentence in it. A package's `words.py` is
  `W = words.of(__package__)`; a file of the shared engine takes `SHARED as S`.
- A table of codes and their words is made when its module is imported — before the step has spoken — so
  it keeps keys and says each when it is read (`W.table({code: key})`, `then=words.cap` for headings).
- Thai sets short clauses side by side and joins names without a space; English does not. What differs is
  a helper, not a sentence: `clauses`, `and_`, `cap`, `date` (“5 ก.ย. 2569” / “5 Sep 2026”), `period`.
- One Thai string has one English (`en` is found by the Thai). Where the same Thai words are said two
  ways in English — a field's name in a sentence, and the twelve characters of a card's heading — the
  English of the one key that differs is in `en_by_key`. Words with no Thai in them (“VAT”) are said as
  they are.
- Text written when a file is *parsed* is read by every later step, so a parsed file keeps the key of a
  sentence, never its words (`inputs.said`, `e2/cards.said`).
- An engine never tests its own sentence as text; where it did (`"ไม่ใช่ 1% หรือ 15%" in note`), it now
  compares with the sentence it would say.
- A figure is asked about by name ("where does the adjusted bank balance come from?"). Its label and the
  names a question finds it by are sentences too, and in a run that is not Thai a figure is also known by
  the names its document gives it (`words.names`) — that paper is what the person is looking at.

**The documents are not in two languages yet, and the table a run computes feeds both.** The same
`computed.json` is what the screen reads (the verdict, the exceptions, what was left out) and what the
working paper is written from. So a run that is not in the documents' language computes twice: as the
screen reads it, and — under `document` — in Thai, for its files alone (`with_document`). `render` writes
the file from `for_document(computed)` inside `speaking(DOCUMENT)`, and says what its gate found in the
run's language. The file of an English run is cell for cell the one a Thai run writes, and a Thai run is
byte for byte what it was (it computes once and carries one table). One exception is known: where a row
of a PDF statement is ("หน้า 1 บรรทัด 4") is written when the file is parsed, in the run's language.
When the documents get their own pass, `document` goes away and nothing else changes.

**What the server does with it.** A package's English is learnt from its manifest (`AddPackageEnglish`:
`en`, and the English kept by key); the shared engine's is told to the server by a list read from
`engine/words.json` (`english_engine_words.go`, kept by a test that writes the list). In English a person
is shown only words the product's own sentences use, so an English sentence this plane did not know would
reach a person as "…": the rule is held on every finding, summary, gate, check and assumption of the
packages' own eval sets run in English (`am14_packages_table_test.go`, written by
`scripts/agent-market/words/dump_english_texts.sh`), each put through the thread's own door. A manifest on its
way to a client loses `strings` and `en_by_key` as it always lost `en`: a screen is sent the sentences
of the runs it reads, not the list of everything an engine can say.

**What holds it.**

- `test_package_words_ratchet.py` — every Thai string left in the code is recorded by fingerprint. A new
  one fails; a recorded one that has left the code fails until the record is written again without it
  (`--shrink`, which refuses to grow). What is left is what an engine *reads* from a person's Thai files
  and what a document *says*.
- `test_am14_words.py` — the code and the words are the same set: every sentence the code names is
  there with exactly the places the code fills, in both languages; none is left that nothing asks for; a
  heading fits in both. A package is held as soon as it has a `words.py`. And the rule itself, per
  package (`EnglishRuns`): the package's whole eval set run in English beside the same set in Thai — no
  Thai reaches a person but their own data and the paper's own words, the run is the same run (the same
  questions by key, answers, figures and end), and the paper is the one a Thai run writes.
- AM14's walk — one run on the built clients, from the agent's page to
  the finished files, with every piece of Thai on the screen sorted: the person's own data, the finished
  document's own wording, or neither.
- The sentences were moved by a script, once: the Thai read from the code and written to the manifest,
  never retyped. (A one-time tool; it is not in the repository.)


## The documents speak the language of the run (AM18, part A)

AM14 left one thing in Thai: the files. A working paper's sheet names, its column headings, the labels
of its face, its notes and footers, the drafted entries' memos, the report's section titles and chart
captions were string literals in the writers, and a run in English computed its table twice so that its
paper could be written from the Thai one. AM18 moved those words too, one package at a time — bank
reconciliation 1.8.0, invoice processing 1.11.0, expense audit 1.10.0, PO matching 1.8.0, financial
report 1.10.0 — and the second table went away.

- A document's word is a sentence like any other: a key under `doc.` in the package's manifest
  (`doc.sheet.*`, `doc.col.*`, `doc.face.*`, `doc.note.*`, …), its Thai exactly as the code held it, its
  English beside it, said when read. The shared writers' words (the report's sections, the sign-off
  sheet, the citations, the deck's notes) are under `docs.` in `engine/words.json`.
- A table's headings are kept by key and said when read (`W.heads([...])`, a `Heads`): `index(text)`
  finds a column by its words in the language being spoken, `of(key)` by its key, so a check that reads a
  finished sheet back reads it in the run's language and never names a column in either.
- Numbers, dates and money follow the language where it is *formatting* — a Thai paper prints BE years
  (`words.date`, `words.period`, `words.period_short` for a chart's axis), an English one the common era —
  and a value never changes. A date a paper printed is read back in either language (`words.date_read`),
  so a bank paper's carried-forward sheet is read by a run in the other language.
- A manifest says which languages its documents speak (`"languages": ["th","en"]`): the agent's page no
  longer tells an English screen that the files will be Thai, and a printed PDF is set in the run's
  language.
- The words an engine *reads* from a person's files — a bank's "ดอกเบี้ย", a policy paragraph's "ต่อวัน",
  a closed PO — are not the document's and stay in the code, in files the ratchet names per package
  (`READS` in `test_package_words_ratchet.py`); a package whose documents speak both languages may hold
  Thai nowhere else.
- `with_document`, `for_document` and `speaking(DOCUMENT)` in a writer are gone; a run computes once.

**What proves the figures did not move** (`tests/dockit.py`). From identical inputs a Thai run and an
English run are compared cell for cell on every case of each package's eval set
(`test_am14_*.py`, `EnglishRuns.documents`): the same sheets in the same order, the same rows and
columns, every number the same to the cell — a day or a month read as the same day in either language,
the numbers of a sentence as a set since the two languages order them differently — and the person's own
data in the same places. The only difference allowed is the document's own words; and the English file
holds no Thai but the person's own (`thai_left`), where their own is what their files, their settings and
their answers held, and what the model read from their scan. The printed paper passes AM9's row-by-row
check and AM14's print proof in English as in Thai (`am9_print_it_test.go`, `printedSelfTestIn`; the IT
lane `scripts/gate/it/am18.sh`).

The words were moved by a script, once — the Thai read from the code and written to the manifest, never
retyped; a module-level literal becomes a bare key, a function-level one `W("key", place=…)`. (A one-time
tool; it is not in the repository.)


## The agent does what it is asked, in the person's own words (AM18, part B)

AM11b and AM13 made an agent do what it is asked of its documents: a model reads the person's words
into the directives the package declares, the code applies them, the thread says what was done and what
could not be, with the reason. Nothing in the code is a list of words a person must type — the model
reads their words, in either language; the package's list names what the code can *do*. Part B hears
three things the words say that were not heard before, and reads the words at a different moment.

**Read first.** The words typed with a round's files are read before any file is (`runner/asked_am18.go:
readAsked`, at the top of the pipeline's loop; AM11b read them after compute). The reading never
touched a figure and still does not — the table is computed the same with or without it — but what the
words ask of the documents' *language* must set the round's before the first sentence is written, a
request that cannot be done is refused before any work, and one that could mean two things is asked
before the round goes on. The `instructions` step is therefore the first step on a run's page.

**The language** (`instructions_am18.go`). The two languages a package's documents are written in
(`languages` in its manifest) are two directives of the platform's — `lang_th`, `lang_en` — read with the
package's own; a language the package does not speak, and any other (`lang_other`), is what cannot be
done, with the reason, and no model translates a document at run time. The language of a round is
decided when it starts: the screen's, unless the install keeps one (below), unless the words say
otherwise — the words win (`takeLanguage` writes it on the run, `SetRunLang`, and every step after is in
it). A finished round's files keep their language: asked for the other, the agent starts a new round from
copies of the same files — a file belongs to one run and goes with it — and says so; the old file is not
touched, and no file of a finished round is made again in another language (its computed table carries
one language's words since part A). Both languages at once is a question, a round being written in one.

**From now on, or this once.** The model says which it read (`Asked.Standing`: "ทุกครั้ง", "from now
on"); what was asked for from now on is kept as the install's own way — the package directives under the
default `instructions`, the language under `language` (the platform's own setting, which may now hold
either language) — and a round starts with that way in force, merged with what its words ask
(`InstructionSpec.Merge`, one of a group). The thread says what it recorded ("Kept as this agent's own
way from now on: … — see or clear it on the agent's settings page") and, when the same words also asked
for something this once, which that was; a later round made with the kept way says so too ("Made as
asked earlier: …"). The two defaults have the source of a remembered answer: the settings page lists them
among what is remembered, with the one button that clears them. Nothing is kept unless it was asked for
from now on; a self-test keeps nothing.

**What could mean two things.** A request that could be two directives of the list — "เรียงรายการใหม่" of
a table that is ordered two ways — is not guessed: the model names the ids (`Asked.Ask`, each declared,
none already applied, the person's words only when they are theirs), and the round asks with a blocking
card of its own (`AskedChoiceFinding`, the question card every other decision uses, at the preflight
moment); the answer is what the file is made with and what the gate reads back.

**What holds it.** `am18_asked_test.go` — the reading: a language the package speaks is the round's and
never a package directive; one it does not speak, or any other, is refused with a reason naming the
languages it speaks; both at once is a question; "from now on" is kept only of what was applied; an
ambiguous request becomes a card of declared options, the first the package lists as what a skipped card
records. `am18_asked_it_test.go` (the IT lane, two chunks) — the bank agent end to end: a round asked in
English, by date every time, is English (its sheets named in English, its gate's words English), kept,
and the next round with no words is the same; asked for Thai, the finished round's file stays and a new
Thai round runs from the same files; cleared on the settings page, a round is as the package makes it; a
request that could mean two things parks the round on a card before any step of work, and the answer
orders the file; another language is refused, with the reason, in the same breath.

**Left as it was, and why.** The platform's older lists of words that route a message — a request for
another format (AM3), for more files, a question — were not touched: they route to a render or an answer,
not to a change of the documents, and the owner asked that nothing be touched that need not be. The one
list that read "English" and answered that the documents are Thai only is gone.


## A run that cannot reach its model waits (AM15)

What was found (test stack, 12 Oct 2026, the in-house model taken away — `scripts/smoke/primary.js
down`): every one of the five accounting agents went on regardless. Their documents may not leave the
organisation, so no outside model may stand in — and none did; the run simply did without. The words
typed with the files came back "unread" and the documents were made the usual way; a scan was read by
OCR alone, with the step still naming the model; the report's commentary was written by rules. Each
run delivered its files and was charged for them (1,316 to 2,733 credits), and none said the model was
away. The gateway had answered each call `504 provider_timeout`.

The rule now: **a run that cannot reach a model it is allowed to use stops, says so, and goes on by
itself when the model is back.**

- **Two different things** (`runner/model.go`). `ErrNoModel`: a model answered and the answer could
  not be used — empty, cut off, refused by the caller's check. The caller's rules stand in for it, as
  always. `ErrModelUnreachable` (a kind of `ErrNoModel`): *no* model the call may use was reached — no
  response, the call's own time ran out, a 5xx or 429 from the gateway, or an answer from another
  model than the one asked. The gateway's refusal of an answer the model did give
  (`output_limit_reached`, `empty_completion`) is the model having been reached. A run whose own
  context ended (shutdown, lost lease) has not found its model away.
- **Where a run stops** (`runner/modeldown.go`, `pipeline.go stepFailed`): the reading of the words
  typed with the files, the reading of a scan, the report's commentary, and a card answered in words
  that no model could be reached to read. The step fails as a step — the first unfinished one — and
  the run is parked: `awaiting_input`, reason `model_unreachable`, a warning notice in its thread in
  the run's language. Nothing after that step has run: nothing is rendered, nothing published.
- **How it goes on**: while any run waits, the runner asks its primary model one word every two
  minutes (`AGENT_MARKET_MODEL_RETRY_SECONDS`) and, on any answer, puts every waiting run back in the
  queue with a line in its thread. Steps already done are read back; the unfinished one asks again.
- **Not for ever**: a run goes on only after the model answered that word, so one that finds it away
  again each time is asking something the model does not answer in time. After three waits
  (`MaxModelWaits`) it ends failed with reason `model_unreachable`, said in words, and can be tried
  again with its files.
- **What it costs a person**: nothing while it waits. A run that waits has delivered nothing, so
  what its finished steps had been charged is held — voided with the wait as its reason
  (`Store.HoldCharges`), which is the platform's cost and counts against nobody's month. When the run
  goes on the rows are put back (`Store.Resume` → `ChargeAgain`, whichever way it was resumed): one
  that delivers is charged for every step, the ones before the wait too; one that is cancelled, or
  ends after its three waits, is charged nothing.
- **In the sidebar**: the count beside an agent ("รอคำตอบของคุณ") and a chat's "รอคำตอบ" are of
  runs that wait for the person; a run that waits for its model is in neither.
- **A message typed after a run**: words that are not a question wait for the model like a step; a
  question is answered from the run's own record where the record can, and where only a model could
  the answer says the model cannot be reached — it used to read "I cannot answer this with
  confidence". A form's mapping still goes to the person to map by hand, as before.
- **On screen**: the run's card is titled "รอโมเดล / Waiting for the model", not "Waiting for your
  answer" — nothing is asked of the person.
- **Held by** `runner/am15_model_test.go` (which answers of a gateway are which of the two) and
  `runner/am15_model_down_it_test.go`, both directions: down — the run waits, nothing made, the thread
  says so in Thai and in English; back — the same run finishes from the step that could not ask, the
  words read, the steps before it not run again; and a cancelled wait, the three-wait bound, a scan,
  the report's commentary. AM15 made the same reading on the test stack through the routes a
  person's screen calls.
- **The integration lane is a list of names**: a sandbox test no chunk names runs nowhere, and a
  renamed test leaves a chunk that matches nothing and passes. `runner/am15_lane_test.go`, in the unit
  lane, fails for either.

## The content and the layout of a deliverable are separate (AM21)

Until this card the code that wrote the words placed them: every slide of the deck sat on the Blank
layout with hand-placed boxes, the report set a size on every run, and each package's workbook writer
carried its own copy of a header style and a list of column widths. A box at a fixed coordinate overflows
when the text is longer than guessed and leaves a hole when it is shorter: the cover's last sentence ran
off the right edge, most pages stopped at half height, and "[AM1b]" sat on the owner's own report for a
month because a company name typed during a walkthrough was the only place a name came from.

- **One file holds the look**: `backend/agent_market/deliverables/look/look.json`. It holds the type
  scale (five roles: display, heading, subheading, body, small; a size per format, a weight per role),
  the themes (navy, teal, charcoal: a palette, a chart palette in a fixed series order, a cover
  treatment, a table style) and the layout catalogue per format, each layout declaring what it takes
  (characters in a heading, items, rows before it breaks, chart series) and so what it refuses. The
  colour of a table header is `themes.<theme>.table.header` in that file. Changing the file re-skins
  every agent's output; no code is touched.
- **The renderer is `deliverables/look`** (tokens, catalogue, measure, dotx, potx, xlsx_styles, markers)
  with the format writers (`report_docx.py`, `deck_pptx.py`, `table_xlsx.py`, `charts.py`,
  `workpaper.py`). `tokens.LOOK` refuses a size or a colour that is not in the file. Nothing outside the
  renderer may set a font, a size, a colour, a coordinate or a box: `tests/test_am21_contract.py` reads
  the generators' code and fails naming the file and line. The five packages' workbook writers and the
  procurement draft write through `xlsx_styles.Book` and `report_docx.ReportWriter`, handing over
  content and its meaning (money, count, text, wrap) and nothing else.
- **The catalogue chooses** (`look/catalogue.py`): content in, the layouts that take it scored by how
  full they are, the best out with a recorded reason (the render's `meta.layouts`); a tie breaks the
  same way every time. Too long for the layout, in order: the next size down the scale (a layout marked
  `shrink`), a break across slides or pages where the layout counts items, rows or lines, and when
  nothing holds it `DoesNotFit`: the run says the content does not fit and names the section
  (`look.fit`), and no file is written for that format (the runner reads `refused` and fails the run
  with the line). The model is told the limits before it writes (`writing_limits` in computed.json,
  then a line of the narrative prompt).
- **The document** is written in the named styles of a Word template built from the file
  (`look/dotx.py`); no run carries a size, a colour or a font of its own (`docx.styles` reads that
  back). The headline figures are printed once, on the cover. A figure in a sentence carries nothing
  inline; the paragraph's references are listed under it (`docx.narrative` holds the list to the
  figures). Every text in a fixed frame, the cover, the tiles, a table cell, is measured in the embedded
  Sarabun and broken between words (`look/measure.py`, libthai).
- **The deck** is made from a PowerPoint template with a master and the catalogue as real layouts with
  real placeholders (`look/potx.py`), 13.333 x 7.5 in, one family named in every part and embedded. A
  slide on a layout outside the catalogue, or a shape outside a placeholder, fails `pptx.layout`; a run
  size outside the scale or a colour outside the palette fails `pptx.scale`. One idea per slide: the
  headline figure, a row of figures, the summary as one statement, at most four points of at most two
  lines with a hanging indent, a table of at most ten rows, a chart large enough to read across a room.
  The footer carries the source file's name; the hashes and references move to a closing slide. The
  chart is the same picture the report carries, drawn at the chart layout's frame; the gate draws it
  again and compares the bytes.
- **The workbooks** (`look/xlsx_styles.py`): one header style, one cell style, number formats by
  meaning (money, quantity, percent, count, text; a negative in parentheses, as the report prints it),
  widths measured from the content so no cell shows `####`. A sheet wider than a page at a readable
  scale has its text columns narrowed and wrapped so it still prints one page across (AM10). A sheet
  name over 31 characters is refused, never renamed. The file opens on its first sheet at A1, and its
  author is the organisation. `xlsx.look` reads all of that back.
- **The PDF** (`runner/pdf.go`): the converter's check reports the fonts and whether each is embedded,
  the ink in each page's outer 0.3 in, and the metadata. `pdf.margins`, `pdf.fonts` and `pdf.meta` fail
  a page with text past its margin, a font not embedded, and an author that is empty, a build marker
  or our own name. A converter that did not measure the margins fails the check.
- **Charts** (`charts.py`): the theme's palette in a fixed series order; colour carries identity only
  and a negative is a sign. No boxed plot area; a baseline and faint horizontal rules; direct labels
  instead of a legend up to four series; values on marks selectively; 300 dpi; one axis.
- **No marker, no product name** on a customer's document: `look/markers.py` reads every text and the
  metadata of a finished file (`look.markers`); the edge's `MarkerIn` refuses a company name with one in
  the admin's look setting and in the chat app's own theme setting, and strips it from an e-mail draft.
- **Seeing the file before sending it** (part 4): `POST /artifacts/{id}/pages` queues a `pages` job
  once; the runner converts the stored file with the same converter and keeps the first pages as PNGs
  beside it (`PageKey`, a manifest); `GET .../pages/{n}` serves them; the files card shows them on
  demand in the web app and the desktop, which renders the same page. The picture is of the delivered
  file itself, never a re-render from the data.
- **Themes and the brand** (part 6.2, 6.3): an admin picks the organisation's theme, its name and its
  brand colours on the admin app's "Look of documents" page (`orglook.go`, audited as
  `agent_market.org.look`). A colour that fails contrast, a typeface the sandbox lacks or a logo too
  small for the cover is refused with the reason, and the theme's own value stays (`theme.resolve`,
  said on the run as `refused`). The same run under navy and teal is the same document cell for cell
  (`tests/test_am21_themes.py`).
- **The gallery** (part 6.5: `tools/look_gallery.py`, `scripts/agent-market/look-gallery.sh`, the
  gate's `look_gallery` lane): every layout with short, typical, long and absurd content, in both
  languages under two themes, rendered, rasterised by the converter and compared with the approved
  pictures in `tests/fixtures/look_gallery`; a difference beyond the threshold fails with both pictures
  side by side; the absurd case must be refused.

## The words every agent writes, and the customer's own form (AM22)

**Every agent's commentary.** The bank reconciliation, the expense audit, the PO matching, the invoice register and
procurement now have a model write their words (`"narrative": "model"`), as the financial report always did. Each
package computes a `figures` table (`engine/figures.py`: key → printed text, its own citation, what it is) and the
facts a model is shown under headings (`narrative_facts`), so a sentence names a figure by its key and the figure is
printed with its own citation (AM8) — procurement, whose prices are cited by their pages rather than by an id, stays
on the quotable path. The words are printed on a sheet of their own after the face of the working paper
(`deliverables/look/commentary.py`, the catalogue's xlsx `commentary` layout), read back by the gate
(`xlsx.commentary`). The rules' words stay as the no-model path: when no model the organisation allows can be
reached, a package that says `"narrative_when_away": "rules"` (the five) delivers with its rules' words and the
working paper says so on its face, under the commentary; the financial report waits for its model (AM15). The line
says which of two things happened: no model could be reached, or a model answered and none of its answers could be
used (the runner's narrative `why`) — the report and the deck say the same. A run a model wrote never carries either
line (`tests/test_am22_words.py`, `runner/am22_it_test.go`).

**A figure a model may name is one the gate can cite.** The financial report offered the model a line that is zero with
no row behind it (income tax in a month that has none), whose citation has no source; a sentence naming it failed the
run's gate. Such a figure is no longer offered (`metrics.py`). A comma after a figure is the sentence's, in both
languages and on both sides: the runner's guard and the gate read "12,480,300, and" as the figure 12,480,300 (they
read "12,480,300," and refused it). Both were found by rendering every measured model answer through the gate, which
is how the words of a new package are to be accepted.

**The validator measures what the prompt promised** (`runner/deliver.go` `checkNarrative`). The summary, each point
and the number of points under a heading are measured against `writing_limits` — the catalogue's own numbers for the
format, in the run's language — in the characters a reader sees (`columns`: a Thai tone mark or a vowel above or below
the line takes no column; `agent_market/columns.py` is the same count). An answer past a limit is refused with the
reason, in the run's language, and asked again once on the same model (`model.go`, bounded); nothing on the way to the
renderer drops, trims or cuts a point any more (the seventh point was dropped until this card). The prompt asks the
model to draft what needs saying and then edit it to the limits without dropping a point, in the run's language.

**One width for both languages** (`deliverables/look/measure.py`). The catalogue's limits are Latin columns; the
chooser weighs a Thai character by how much wider a Thai column is in the embedded Sarabun (measured from the font on
the engine's own sentences, about 1.24), and a Thai run is told its budget divided by the same ratio, so the two
languages are allowed the same width of text. Height is measured as lines × size × 1.22 for both scripts; Sarabun's own
line box is 1.30 em and Thai with stacked marks reaches it — left for its own card.

**A customer's form: not applicable is not missing** (`template_out.fill`, `template_fill.drop_not_applicable`). A
package names the fields its round has no concept of (`template_gaps` → `not_applicable`: an exchange rate on a baht
account, a cash advance the expense audit does not see, a credit-note section with no credit note); a slot mapped to
one is taken off the form's face — an Excel row hidden (its formulas stay put), a Word row that holds only that place
removed — recorded in the run's JSON and checked off the face (`template.not_applicable`). A slot that should have a
value and has none is printed on the face under its heading, as before. The filler never decides which is which.

**The three new forms** (bank, expense, PO: `template_values` / `template_gaps` / `template_wanted` /
`template_why_not`). Every value offered is a computed figure with its citation (the bank cites each kind of
reconciling item, `t_<kind>`). A field one slot takes is marked `"once"`; a mapping that gives it to two slots is
refused at the save naming both, and a model's proposal of it for two slots keeps neither (`OnceTwice`, `keepValid`) —
two labels that could take one balance are the person's to tell apart. A blank inside a merged range that is not its
top-left is refused by name; a blank that is a merged range is filled at its top-left and read again as still one
range; a label drawn across several columns finds its blank past its own merge. A new version whose addresses are the
same and whose labels moved is `moved`, not `kept`, and its old mapping is not carried. A round with nothing for the
form makes no file and says why, on the form's card and in the round's message. The test forms are made up
(`tests/am22forms.py`, committed under each package's `evals/fixtures/forms`).

**A customer's form: what is kept on purpose.** Three things were looked for at the vendors and not found; they exist
because of bugs found here, and `tests/test_am22_kept.py` with `runner/am22_test.go`
(`TestTheFilledFormIsRecalculatedAndReadBack`) fail if any is taken out:

1. **The finished file is opened again and the values read back** (`template_out.verify`): a value the filler wrote and
   the file does not hold is a gap, whatever the filler reported.
2. **A formula cell and a locked cell are refused; `needs_recalc` is set and the converter recalculates the form**
   (`runner/template.go` `recalcTemplate`): the form's own arithmetic is never written over, and its results are the
   form's, not ours.
3. **A new version is inspected again and diffed** (`template_out.diff`): kept, added, removed and moved — never a
   mapping carried by a key whose cell now means something else.

## The file a person asks for, from any agent (AM23)

**A kind of file the agent does not make is refused, never dropped.** Until AM23 a file set naming a kind of file the
agent's manifest does not declare reached the engine, which made what it makes and dropped the rest without a word: a
bank reconciliation whose set said Word alone finished "done" with no file. The settings page refuses such a set by
name with what the agent makes (`settings.go` `check`, `outputset.go` `NotMadeText`); a round whose set still names one
says so before any work, and one left with nothing it can make stops there, charged nothing (`format_not_offered`,
`runner/pipeline.go` `refuseNotMade`). Every file the engine is asked for comes back made, refused with its reason, or
left out with why (`not_made`: procurement's draft before anything is chosen) — anything else is a render that failed
(`runner/deliver.go` `unaccounted`).

**No model's name in any file** (`deliverables/look/commentary.py` `no_model_name`, run on every file every agent makes,
read back from the finished file). The commentary sheet, the report's last section and the deck's closing slide no
longer say which model wrote; the expense audit and the invoice register say the organisation's AI assistant re-read
their scans, not which model did. The run's own record keeps the model — its steps, `model_used`, its progress card,
the admin's run view and the cost lines — and the files card says what saw the documents and where it ran, by what it
is, never by name.

**One neutral line, the organisation's to switch off** (`org_settings.assistant_line`, migration `013_am23`, on by
default; the admin's Models page, beside "may documents leave", every change in the audit log). A file whose words a
model drafted carries one sentence in its closing block — the commentary sheet's references block in a workbook, the
report's last section after the sources and their hashes, the deck's closing slide: "the commentary was drafted by the
organisation's AI assistant; every figure is from the source cited beside it". It names no model, no vendor and not
the platform, and it is never on the cover. When the system's rules composed the words, the file says so under them
whatever the switch says — "the words of this section were composed from the system's rules, not written for this run"
— because that changes what a reader should trust; why no model's words were used is the run's record. The control
plane reads the switch apart from the organisation's row (`org.go` `assistantLineQ`), so one deployed before the runner
has applied 013 still reads the organisation, with the line on. Fixtures: `tests/test_am23_closing.py`,
`runner/am23_it_test.go`.

**Every agent makes the file a person asks for** (`deliverables/document.py`, each package's `scripts/document.py`). The
bank reconciliation, the expense audit, the invoice register and the PO matching make a report and a deck as well as
their workbook. A package says what its content *is* — the title and who and what period, two or three figures for
the cover, the one table a reader needs (the reconciling items, the claims or invoices that did not pass, the
documents with a note), and the bank's reconciliation statement — and `document.py` writes the rest from the run's
computed table: what the run found (the package's own `narrative_facts`), every figure of the `figures` table with
its reference, the commentary, and a closing block with the files' SHA-256, the assumptions and the references. The
report and the deck go through the financial report's writers and the layout catalogue; a package sets no font, size,
colour, coordinate or box. A document is never the workbook's sheets laid into Word: it reads no sheet, shows the part
of a long table a reader needs (the largest twenty, or the order a person asked for) and says the workbook has every
row; the deck holds every row of its table or refuses. The workbook stays the authority, and a round nobody asked
anything of makes the workbook alone — its cells are the same with or without a document beside it, and a one-file
agent is never asked which files its round makes (`OutputSetFinding`). Fixtures: `tests/test_am23_documents.py`,
`runner/am23_it_test.go`.

**A PDF is how a document prints, not a fourth writer.** The manifest says which document an agent's PDF prints
(`outputs[].of`: the report for the financial report, the workbook for the four), and a PDF that names no document the
agent makes is refused when the manifest is read — a PDF with nothing to print cannot be declared. The runner renders
that document (made and not delivered when only the PDF was asked for), converts it with the converter, and gates the
PDF: a report's PDF holds the report's paragraphs in order (AM2), a workbook's PDF renders every page as the workbook is
set to print (its print area, its header row on every page, one page wide — AM21) and holds every figure exactly as
the workbook prints it (`runner/pdf.go` `pdfPrintGate`). Nothing in the engine writes a PDF. The picker shows a PDF
under the document it prints ("…and a PDF of it"), and the files card calls it what it prints.

**Asked for, cannot be done: refused before any file is written, with what can be made instead**
(`deliverables/document.py` `plan`, each package's `plan`, `runner/plan_am23.go`). A deck's table breaks across at
most two slides (the catalogue's `parts_max`: PowerPoint's ladder ends in spilling, this one in a refusal); a deck
asked for with more rows than that, or a report or a deck of a round with nothing to say (everything matched, no
exception, and a commentary with no point to make), is refused by the package in the run's language, with two to four
options computed from the round's own figures — the workbook alone first (what the round makes anyway), then the
deck of the rows at or above the smallest round amount that leaves at most a deck's worth (or the amount of the
twentieth largest row when no round amount does), or the Word report. Asked with the round it is a blocking question
of the clarification engine, asked after the commentary is written and before the files are made; it is never answered
by its default (re-asked at most twice, then left for a tap). Asked of a finished round — typed, from the "more files"
menu, or a card — it is a card on the round whose answer makes what was picked. The words are the package's, the same
whichever way the file was asked for. The words typed with a round's files are a format request too: what the agent
makes joins the round's set, what it does not is refused by name with what it makes (the settings page's sentence),
and a `.xls` is answered with why it is never made. Fixtures: `tests/test_am23_refusals.py`,
`runner/am23_it_test.go` (a deck of 37 reconciling items, `kbank_sep_many_fees.xlsx`).

**.xls and .doc come in; they never go out** (`runner/convert_am23.go`, `engine/step.py` `_as_original`). Thai
accounting still passes both around, and until AM23 they were refused at the door. The agents take an `.xls` wherever
they take an `.xlsx` and a `.doc` wherever they take a PDF; the converter already there makes a copy on the way in (an
`.xlsx`, a PDF) once, as a recorded step, and the engine reads the copy under its converted kind. What the run records
and cites is the original — its name and its SHA-256, the copy's kept beside it — and the round says the file was
converted. A file that will not convert is refused by name with what failed, and never half-read: the converter's own
refusal of it, or a copy that holds nothing or that LibreOffice read as undecodable text (it "converts" a damaged
`.xls` that way without a word — found by this card's fixture `broken.xls`). The round goes on without it, asking for
it when it was what the round needed. Neither kind is ever written: no manifest may declare one, and a request for a
`.xls` is answered with why — 65,536 rows, no frozen header row repeated on every page, no print area — and the
`.xlsx` is made instead. Fixtures: `runner/am23_it_test.go` (`kbank_sep.xls`, `broken.xls`), `runner/am23_test.go`.

**Every file of a round says the same number** (`deliverables/agree.py`, run by `engine/step.py`'s render op on the
finished files; `runner/agree_am23.go`). Since AM23 an agent makes two or more kinds of file with figures in each, and
a figure printed one way in one and another way in the next is the worst bug this product can have — AM14's was
`3600` in one place and `3,600.00` in another. So every finished file is read back — the workbook as Excel prints each
cell under its number format, the report's tables, cover and cited paragraphs, the deck's slides, the customer's form
as its own gate found it filled — and every printed number that is one of the run's figures is compared: beside the
figure's label (the figure's own, or the label the package's workbook face gives it, `face(view)` in its
`scripts/document.py`), or in a line that cites it, where the same amount printed with more or fewer decimals is the
same figure (3,820.55 and 3,821) and a small whole number counts only when it prints exactly the same. A figure
printed two ways fails both files that print it — no winner is picked — naming the figure, the two places and the two
strings, and the round fails its gate. A deck made after the round is compared with the round's own workbook and
report (the runner hands them to the render as `prior`). A zero the workbook prints as the accounting dash is not a
second way of printing it. What the check found first: the financial report's workbook printed satang (3,820.55)
where its report and deck printed whole baht (3,821); the workbook now prints each figure the report's way — whole
baht, a percentage to one decimal, a ratio's change in points to two (`look/xlsx_styles.py` BAHT, PCT1, POINTS) —
while each cell keeps its exact value, and the ledger and the recomputation sheet keep the satang. (Printing satang in
the report instead was tried: its P&L table of six amount columns no longer fits a portrait page.) The print proof
states each amount at the decimals its cell prints (`workpaper.money_value`). Its customer's form is filled with
the figures as printed, whole baht (the form wrote satang beside them until the check caught it on a filled memo). Tests: `tests/test_am23_agree.py` (Part 4 fixture 6: a figure written without its commas, with another
value, under another number format, in the form; the financial report's old two-decimal workbook),
`runner/am23_it_test.go`.

**What makes a commentary read machine-made** (AM23 §3.3; `runner/narrative_am23.go`, `deliverables/look/commentary.py`).
Measured over AM22's 246 model commentaries: ten points each where the rules write five, one in eight only saying
again what the round's facts, its figures' labels or the commentary's own summary already say, and the same headings
on every document. The validator now holds an answer to what a reader would. **Padding**: a point of the findings or
of what to watch that adds next to nothing to what is already said — in English at most two words, in Thai (written
without spaces between words) at most six character trigrams, filler aside — is sent back once with its place, the way
a point too long is, and never cut; a next step is something to do and never padding. **Hedges**: each package
declares the hedges its commentary never writes (`narrative_hedges`, both languages: "ควรพิจารณาตรวจสอบเพิ่มเติม",
"further review"); the runner refuses an answer with one, saying what to write instead, and the engine's gate reads the
same list in the commentary every finished file prints (one list, the manifest's; no screen is sent it). **Headings**:
each part's heading is written from what it found — the model's (`headings` in its answer, held to the tightest title
it is printed under, `heading_chars`, and to the number guard), or the rules' (each package composes them from the
round's figures: "ลงตัว…", "รายการกระทบยอด {items} รายการ", "ข้อยกเว้น {exception} จาก {claims} รายการ") — and the
generic heading is printed only when a part has none of its own; a part that found something under the generic one,
or under none, is asked for once more. Every file a commentary is printed in prints the same headings, and the gates
read them back. The organisation's own voice is the customer's to add, not this product's. Tests:
`runner/am23_narrative_test.go`, `tests/test_am23_commentary.py`.

**Thai a reader sees as a typo** (AM24 §1.2; `runner/marks_am24.go`, `engine/e2/marks.py`). The in-house model
wrote a Thai mark twice on one letter ("ร่่าง", "กัับ") in 4 of the 7 commentaries it wrote on live after AM23 — one
Thai sentence in nine, measured over 123 cases, whichever card's prompt. Whether a commentary marks Thai as Thai writes
it is read by the rules the PDF repair already holds (a cluster's marks vowel first, then the tone mark, then the
nikhahit; at most one of each; a nikhahit only as the first half of sara am; mai taikhu with no tone mark), through
the engine's platform op `thai_marks`, run with the runner's current engine — so an install pinned to an older engine
is checked the same. An answer with a slip is sent back with the words it is in, in the run's language, through the
retry that exists (`askNarrative`, which the narrative step and its tests both run). When no attempt is free of
slips, the last answer whose every slip has exactly one reading — a mark written twice, a cluster's marks in another
order — is used with those slips written as Thai writes them, and each text is recorded on the run's narrative step
as written and as used (`marks_mended`); a slip with no one reading (two tone marks, a mark on a vowel or after a
space) is never mended, and such an answer is not used. A check that could not run says so (`marks_unchecked`); it
is never recorded as one that passed. It is not charged to the run. Each reading is a sandbox call (≈ 2.5 s here),
so only an answer that could be used is read: one only too long is asked again for its length and read once, if
it is the one used. What it cannot see: a mark the model dropped
("ไม" for ไม่) is well-formed Thai to any rule of marks — only a dictionary could tell, and none is in the engine.
Tests: `runner/am24_marks_test.go`, `tests/test_am24_marks.py`.

**An agent's page does not hold a swap** (AM24 Part 2; `api.go` EndStreams). A thread's stream runs until its page
goes or ten minutes pass, and `http.Server.Shutdown` waits for every open request: an agent's page left open kept the
old chat-edge's stop waiting its full ten seconds (the swap of 17 Oct 2026: 11.7 s without an answer, the old copy's
stop 11.4 s of it; AM12's 11.9 s the same). The server registers `EndStreams` with `RegisterOnShutdown`: once it has
stopped listening, each open stream sends the `reconnect` it already sends at its deadline and ends, and the page
reconnects with its cursor through the front door, to the copy that answers next. Test: `am24_streams_test.go`.
