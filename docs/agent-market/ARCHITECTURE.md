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
  too — presentation, not behaviour. `scripts/probes/AM10/icons.js` renders the
  set together at 32 px and at full size, light and dark.

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
- **What is not in English yet.** What a package's *engine* writes while it
  runs: a check's question, the labels of a run's figures, the lines of the
  quality check on the files card, and every word inside a generated document
  (about two thousand strings woven into the code that computes and re-reads
  them). No package lists English in `languages`; an engine step is told the
  run's language (`"lang"`) and does not act on it yet. On an English screen a
  run therefore reads English around Thai — everything the platform and the
  manifest say in English, the engine's own lines and its documents in Thai —
  and an agent's page says so before a person installs it.

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
- **Not ordered: the card a person chooses from in the thread.** It is made
  before the words are read (the choice comes before the files), and it is
  still in the computed order — fewest unknown points first. The package's
  note says so.

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
