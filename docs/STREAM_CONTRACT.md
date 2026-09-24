# STREAM_CONTRACT — the one document both sides answer to

**Scope.** Every frame that reaches a chat page, whether it came over SSE
from `chat-edge-api` or over the Electron bridge as `turn.event` from the
desktop host. One envelope, one set of terminal events, one resume rule.

**Not in scope.** OpenAI-style background runs (a run that keeps going for
hours after the client is gone). Our runs live and die inside one turn.
This contract only makes a run **resumable without gaps or duplicates**,
and **honest about how it ended**.

Written for FIX41. Baseline measurements that motivated every rule:
`docs/fix41/A-baseline.md`.

---

## 1. Vocabulary

| term | meaning |
|---|---|
| **run** | one generation. Starts when the server decides to produce an assistant message, ends at exactly one terminal event. A conversation has many runs; a run belongs to exactly one assistant message. |
| **run_id** | opaque id of the run. NOT the conversation id, NOT the message id. A regenerate of the same message is a NEW run with a NEW run_id. |
| **seq** | 1, 2, 3, … monotonic, gapless, per run. |
| **terminal** | the last frame of a run. Exactly one, always sent. |
| **ledger** | the ordered list of `(seq, event, data)` for a run, kept for a bounded time so a client can be handed the frames it missed. |

---

## 2. B1 — the envelope

Every frame carries these four things:

```
run_id   string   id of this generation
seq      int      1, 2, 3, … forward only, no gaps, no repeats, per run
event    string   the event name (unchanged from before FIX41)
data     object   the payload (unchanged from before FIX41)
```

**Wire shape — SSE (chat-edge).** `run_id` and `seq` are injected as
top-level members of the existing `data` object. The frame keeps its
current form; nothing is nested or renamed:

```
event: content_delta
data: {"run_id":"6f2c…","seq":7,"delta":"สวัสดี"}
```

**Wire shape — desktop bridge.** `turn.event` already carries `seq`
beside the payload; FIX41 adds `runId` in the same place:

```ts
{ type: "turn.event",
  payload: { conversationId, runId, seq, event, data } }
```

The bridge keeps `seq` on the envelope rather than inside `data` because
that is where it already was and installed pages read it there. Both
placements are the contract; a client reads whichever its transport uses.

### Rules

1. **`seq` starts at 1**, and **`message_start` is always seq 1.**
2. **Heartbeats do not consume a seq.** `: ping` is an SSE comment frame,
   not an event; it never enters the ledger and never appears in a replay.
3. **`run_id` is announced in `message_start` and repeats on every later
   frame of that run** — including frames replayed by a resume.
4. **A replayed frame keeps its ORIGINAL `seq` and `run_id`.** A resume
   never mints new numbers. This is why the ledger stores events rather
   than a folded snapshot (see §5).
5. Fields are **additive**. A client that ignores `run_id`/`seq` behaves
   exactly as it did before FIX41.

---

## 3. B2 — terminal events: however it ends, it says so

Every run ends with **exactly one** of these, and it is **always the last
frame**:

| event | when | payload (in addition to the envelope) |
|---|---|---|
| `message_complete` | the turn produced its answer | as before + `final_seq` |
| `error` | the turn failed | as before (`code`, `message`, `details?`, `attempts?`) + `final_seq` |
| `cancelled` | **new** — a person pressed Stop | `message_id`, `partial: true`, `final_seq` |

* `final_seq` equals this frame's own `seq`. A client that received
  `final_seq` frames numbered 1..final_seq knows it has the whole run.
* **A silent pipe is never a valid ending.** Every `return` in a stream
  handler must have emitted a terminal first; this is enforced with a
  `defer`, not by inspection.
* **`cancelled` is a new event name.** Names that already exist are never
  renamed — an installed client must keep working (§7).

### What changed, and why the old shape was wrong

Before FIX41 a failed turn ended `…, error, message_complete` and a
cancelled turn ended `…, error("stream error: context canceled"),
message_complete`. So:

* the last frame of a **failed** run was the **success** terminator, and
* a person pressing **Stop** was reported to every other viewer as a
  **crash**, and then as a **success**.

Both are measured in `docs/fix41/A-baseline.md` (P3, P4).

Under this contract the drain no longer writes its own `error` frame for
a stream-level failure; the handler writes **one** terminal that names
what actually happened. A provider error therefore reaches the client a
few milliseconds later than before (after the files/sources read-back)
and as one frame instead of two. That is a deliberate behaviour change,
recorded here rather than left to be discovered.

---

## 4. B3 — resume

```
GET /api/v1/conversations/{id}/messages/{messageID}/stream[?starting_after=<seq>]
```

| case | behaviour |
|---|---|
| `starting_after` **absent** | pre-FIX41 behaviour: replay from the start of the run, then live tail. Kept so an un-updated client works unchanged. |
| `starting_after=N` | replay **only** events with `seq > N`, in their original order and with their original `seq`, then continue with the live tail. |
| `starting_after` ≥ the run's `final_seq` | the recorded terminal event is returned immediately. Not an error — the client simply already had everything. |
| `starting_after` older than the ledger still holds (trimmed by TTL or cap) | **`409 RESUME_TOO_OLD`.** The client must load the message from the database instead. It must NOT be told the stream is complete. |
| no run is attachable yet | `409 RESUME_UNAVAILABLE` (unchanged). |
| the subscriber fell behind | `RESUME_LAGGED` (unchanged). |

**Desktop.** `turn.resume` keeps its existing name and its existing
`ui.ready{conversationId, lastSeq}` payload. `lastSeq` means exactly what
`starting_after` means. The desktop answers by the same rules, including
"too old" — a host that no longer holds the run must say so rather than
replaying nothing and letting the page believe it is up to date.

**Client cursor.** A client tracks `(run_id, seq)`, not `seq` alone. When
the `run_id` changes the cursor resets to 0. This is what makes the
cursor correct once a ledger can be reloaded from disk (Part F) and seq
no longer necessarily continues from the previous process.

---

## 5. Why a ledger and not a snapshot

Before FIX41, `StreamBroker` kept the *latest state of each kind*: one
string builder for the content, one sticky value per phase marker, and
`map[id]latest-frame` for tool cards. A resume replayed those in a fixed
canonical order.

Measured consequence (P1b): a run whose live stream was

```
content_delta, tool_activity, content_delta, code_execution,
tool_activity, content_delta, code_execution, content_delta
```

came back from a resume as

```
code_execution, tool_activity, content_delta
```

— eight frames became three, and the tool cards moved ahead of all the
text. No amount of care in the replay code can fix that: **the ordering
information was never stored.**

So the broker keeps an ordered `[]{seq, event, data}` of the frames it
actually sent. The folded snapshot stays (it is what serves a client that
sends no `starting_after` cheaply, and it is the fallback for a run that
predates the ledger), but the ledger is the authority for ordering and
for `starting_after`.

**The ledger stores exactly the bytes that went out over SSE.** Those
bytes have already been through the gateway's DLP masking; chat-edge has
no masking of its own and must never hold a rawer copy than the wire.

---

## 6. B4 — name map

"Contract name" is what this document calls the thing. The other two
columns are the names actually on the wire today. **Where they differ,
the existing name wins** — nothing is renamed.

| contract name | chat-edge (SSE) | desktop (`turn.event`) | note |
|---|---|---|---|
| run start | `message_start` | `message_start` | seq 1 on both |
| text | `content_delta` | `content_delta` | |
| reasoning text | `reasoning_delta` | `reasoning_delta` | |
| reasoning phase | `analysis_status` | `analysis_status` | |
| model chosen | `model_resolved` | `model_resolved` | |
| fallback used | `fallback_info` | `fallback_info` | |
| reasoning downgraded | `reasoning_notice` | `reasoning_notice` | |
| attachment failed | `image_notice` | `image_notice` | **payloads differ** — chat-edge `{failures:[{filename}]}`, desktop `{filenames:[…]}`; the page only reads the chat-edge shape, so the desktop notice never renders. Pre-existing, out of FIX41 scope, recorded here so it is not lost. |
| generic tool progress | `tool_activity` | `tool_activity` | |
| python run | `code_execution` | — | chat-edge only |
| image / video / search phase | `image_generating` / `video_generating` / `search_status` | — | chat-edge only |
| provider went quiet | `provider_stall` | — | chat-edge only |
| files produced | `message_files` | — | chat-edge only |
| citations | `message_sources` | — | chat-edge only |
| context was compacted | `compaction_status` (*"summarizing" — in progress*) | `compaction_notice` (*"n messages dropped" — after the fact*) | different events on purpose; both consumed by the page |
| older messages left out **without a summary** | `compaction_notice` `{dropped, sent, total, summary: "failed"\|"partial"}` — FIX102, sent only on that loss, sticky in the snapshot | `compaction_notice` (no `summary`; every compaction) | same event, same fields; the page words it by `summary`, and on reload reads the same fact from the `compacted_history` source's metadata |
| step / waiting | — | `status` | desktop only |
| plan checklist | — | `plan_update` | desktop only |
| tool result card | — | `tool_output` | desktop only |
| context budget | — | `context_budget` | desktop only |
| **run finished** | `message_complete` | `message_complete` | terminal |
| **run failed** | `error` | `error` | terminal |
| **run stopped by a person** | `cancelled` *(new)* | `cancelled` *(new)* | terminal |

**Dead branch:** the page has a handler for an event named `paused` that
no server has ever emitted. Left alone by FIX41; noted so a future reader
does not assume it is load-bearing.

---

## 7. Compatibility with clients that have not been updated

The desktop app loads its page from `config.pageUrl` — a remote
web-chat build — so **the page always matches the deployed web-chat**,
never the installed binary. What does *not* update with the server is the
desktop **main process** (the installed `turn-coordinator` / bridge host).

| combination | effect |
|---|---|
| new chat-edge + old page | `run_id`/`seq` are extra JSON members and are ignored. `error` as a terminal already finalises the message on the old page (it always did). A **cancel seen by a viewer that did not press Stop** is the one degraded case: the old page ignores `cancelled` and only settles when its existing 180 s dead-stream watchdog fires. It recovers; it recovers late. |
| new chat-edge + old desktop host | the host consumes chat-edge SSE only when relaying an edit-fork; it ignores unknown event names and emits its own `message_complete` at the end regardless. Unaffected. |
| old chat-edge + new page | no `run_id`/`seq` present → the page's cursor stays 0 and it behaves exactly as before. |

The `cancelled` degradation is the price of B2's "exactly one terminal"
rule. It is stated here rather than worked around, because the
alternative — sending `cancelled` *and* `message_complete` — would break
the rule that makes `final_seq` meaningful.

---

## 8. Bounds

| knob | default | why |
|---|---|---|
| `STREAM_LEDGER_TTL_SECONDS` | 600 | a run is resumable for ten minutes after it ends, then it is gone. Matches the window OpenAI's background runs keep. Long enough for a refresh, a tunnel, or a laptop lid; short enough that memory is not a store. |
| `STREAM_LEDGER_MAX_EVENTS` | see `docker-compose.yml` | a cap so one runaway generation cannot grow without limit. Past the cap the OLDEST events are dropped, and a `starting_after` that points into the dropped region gets `409 RESUME_TOO_OLD`. Dropping the oldest (not refusing to record) keeps the *live tail* correct, which is what a reattaching client needs most. |

Both are configuration, not constants in code.

### 8.1 Bounds of durability — what the Redis mirror does NOT promise

The in-process ledger is the authority while the process lives. The Redis
copy is written **behind** the turn by one background writer that flushes
every **25 ms or every 64 frames**, whichever comes first
(`redisRunStore.run()`), so that a frame costs the turn ~17 µs instead of
~1 ms. That design has two edges. Both are the intended behaviour, and
both were unwritten until FIX42; they are declared here so nobody reads
them as a bug later.

**Case 1 — the ≤ 25 ms window.** If the process dies while frames are
still in the writer's channel, the Redis copy is short by up to one flush
interval. A client that reattaches with a cursor *past* the stored end —
it rendered `seq 6`, the mirror holds `1..4` — gets an **empty replay** and
`Interrupted=true`, i.e. `error/GENERATION_INTERRUPTED`. This is the
**honest failure**: the answer it already rendered stays on screen, and
the stream tells it the generation did not finish. What is *not* on offer
is a resume that pretends frames 5–6 were never sent. The window is
bounded by the flush interval and the exact bound is "whatever was
produced in the last ≤ 25 ms before the crash".

> Note on numbering in this case: the synthesised terminal continues from
> the stored end (`FinalSeq` is 0 for an interrupted run, so it is
> numbered as if the run were empty), which is *below* the client's cursor.
> The web page still acts on it — `noteFrame` advances the cursor but does
> not gate dispatch — so the user sees "interrupted" as intended. A client
> that enforces §9 rule 2 literally would drop it; see OPEN_DECISIONS #40
> for whether that is worth tightening.

**Case 2 — writer overflow.** The writer's queue holds 16 384 frames. If
it fills (Redis stalled, or tens of turns at full tilt), `Append` does
**not** block the turn and does **not** keep a partial copy: it marks the
run as dropped and **DELs the whole key**, so a resume for that run falls
back to the folded snapshot (§5) rather than to a ledger with an invisible
hole. A truncated answer with no sign of the gap is the failure the ledger
exists to remove; refusing to store one is the right call. Until FIX42
the only trace was a `slog.Warn` line per run. It is now **counted**:
`run_ledger_overflow_drops` on `GET /health` (process-wide, monotonic,
resets on restart). Zero is the expectation; the threshold at which a
non-zero value pages someone is open — OPEN_DECISIONS #40.

---

## 9. What a client must do to be correct

1. Remember `(run_id, seq)` of the last frame it **rendered** — not the
   last frame it received. A frame that never reached the UI must not
   advance the cursor.
2. Drop any frame whose `seq` does not advance the cursor for the current
   `run_id`. This makes delivery idempotent, which is what allows a
   replay and a live frame to race without harm.
3. Reset the cursor to 0 when `run_id` changes.
4. Send `starting_after` = the cursor when reattaching.
5. Treat `409 RESUME_TOO_OLD` as "reload this message from the API",
   never as "the answer is finished".
6. End the stream on any of the three terminal events — all three, not
   just `message_complete` and `error`.

---

## 10. Terminal cause — however it ends, it is written down (FIX118)

§3 made every run end on exactly one terminal frame. FIX118 makes the
*reason* a fact both surfaces record the same way, and closes the one
ending §3 did not name: the provider closing the stream without saying
anything.

### 10.1 The closed set

`docs/contracts/terminal_cause.json` is the source of truth. chat-edge
(`internal/service/terminal_cause.go`), the desktop host
(`apps/desktop/src/main/terminal-cause.ts`) and the page
(`apps/web-chat/src/lib/terminalCause.ts`) each carry a copy pinned to
that file by a test.

| cause | meaning | row status |
|---|---|---|
| `completed` | the run produced its answer and said so (`message_complete` / `[DONE]`) | `completed` |
| `stopped_by_user` | a person pressed Stop | `cancelled` |
| `error` | a terminal `error` frame named the failure; `error_code` says which | `error` |
| `timeout` | nobody spoke for too long — an idle watchdog or a deadline ended the run | `error` |
| `disconnected` | the connection to the provider broke before it said it was done (reset, unexpected EOF, a transport throw) | `error` |
| `eof_without_terminal` | the provider closed the stream cleanly with no `[DONE]`, no error frame and no finish reason | `error` |
| `interrupted` | the process that owned the run died; found later by a resume or a ledger replay | `error` |

### 10.2 On the wire

* `error` carries `cause` (one of the set) beside `code`, `message`,
  `retryable`, `details`. A transport-level failure — one with no
  gateway code — is classified by the side that saw it into a **catalogue
  code** (`backend/app/core/error_catalog.py`): `stream_interrupted` for
  `disconnected` and `eof_without_terminal`, `provider_timeout` for
  `timeout`, `GENERATION_INTERRUPTED` for `interrupted`, and
  `unclassified_error` when nothing could name it (the side logs the raw
  error type and text when that happens, so the missing code is visible).
  The raw transport text never rides `message`; it rides
  `details.transport`.
* `cancelled` carries `cause: "stopped_by_user"`.
* **EOF is not a terminal.** A stream that closes with no `[DONE]`, no
  error frame and no finish reason ends on `error` /
  `stream_interrupted` / `eof_without_terminal`. Before FIX118 both the
  chat-edge pump and the desktop reader synthesised a success there, so a
  cut stream persisted as `completed` with half an answer.
* The desktop host rewrites its own former codes before they leave
  (`network` → `stream_interrupted`, `host_exception` / `no_result` →
  `unclassified_error`, `generation_interrupted` → `GENERATION_INTERRUPTED`),
  keeping the original under `details.host_code`, so the page shows one
  sentence for one cause whichever surface produced it.

### 10.3 In the database

`chat.messages_masked.terminal_cause` and `.error_code` (migration 043)
are written by every finish path and by `MarkInterrupted`. They ride the
message list wire (`terminal_cause`, `error_code`) so a reload shows the
same sentence the live frame did. `POST /conversations/{id}/messages/import`
accepts `status` (`completed | error | cancelled`), `terminal_cause` and
`error_code` on assistant items; a user item's are ignored; values
outside the sets are `422`. Before FIX118 every imported row was
`completed` by SQL literal.

---

## 11. One id per turn, and why the model stopped (FIX119)

### 11.1 The turn id

`run_id` (§2) is now also the request id. For a web turn the chat-edge
Logging middleware mints it once (or honours a client-supplied
`X-Request-Id` of the shape `[A-Za-z0-9._-]{8,64}`), and that one value is:

* the `X-Request-Id` on chat-edge's response and in its `http_request` line;
* `run_id` on every frame of the run (the envelope, §2);
* the `X-Request-Id` chat-edge sends the gateway on every call the turn
  makes (each tool round included);
* `request_id` in the gateway front's `http_request` line;
* `turn_id` on every backend log line of that request, joined to the
  backend's own `request_id` (`req_…`, the `request_logs` key) by one
  `turn_request_bound` line — the backend keeps minting `req_…` per call
  because one turn makes several calls;
* `chat.messages_masked.run_id` on the assistant row (migration 044),
  and `run_id` on the message list wire.

For a desktop turn the host's `runId` plays the same part: it is sent to
the gateway as `X-Request-Id`, to chat-edge as `X-Request-Id` on the
import and as `run_id` on the imported item, and the bridge splices it
into every frame's data so the page reads one shape for both surfaces.

The backend's `req_…` comes back on the stream response header
(`X-Request-Id`, the value starting `req_`) and on error frames
(`request_id`); chat-edge logs it beside the run id
(`gateway_stream_opened`, `gateway_stream_done`) and forwards it under
`details.gateway_request_id` on a terminal `error`. The provider's own
request id, when the provider returned one (`x-request-id`,
`request-id`, `cf-ray`), rides the backend's final chunk as
`provider_request_id` and is logged in `stream_provider_complete` /
`provider_complete`.

**On the page** a failed turn's card shows the run id as a reference the
person can copy. `grep <run_id>` across the four logs finds the turn.

### 11.2 Stop reason

`stop_reason` is why the MODEL stopped — distinct from `terminal_cause`
(§10, why the RUN ended). A completed run can carry
`stop_reason: max_tokens`: the answer stands, cut off. Closed set
(`docs/contracts/terminal_cause.json → stop_reasons`):

`end_turn | max_tokens | tool_use | stop_sequence | content_filter | refusal | context_window_exceeded | unknown`

* The backend maps each provider family's native value into the set in
  ONE table (`app/services/providers/stop_reason.py`, pinned to the
  contract). A value the table does not name is `unknown` and logged
  (`stop_reason_unmapped provider= value=`); a provider that said nothing
  is `unknown`, never `end_turn`.
* Wire: beside the OpenAI-shaped `finish_reason` (which stays, for API
  compatibility) as `choices[0].stop_reason` on the final streaming chunk
  and on the non-streaming choice — where `finish_reason` itself used to
  be dropped for `"stop"`.
* chat-edge forwards it on the Done, `message_complete` carries
  `stop_reason`, the row stores it (migration 044) for a completed run,
  and the list wire returns it. An older gateway that sends only
  `finish_reason` is read through the contract's `openai` row.
* The desktop host reads it from the final chunk (same fallback) and puts
  it on `message_complete` and on the imported item.
* The page tells the person when it matters: `max_tokens` ("the answer
  is not finished"), `refusal` (not a system error), `content_filter`,
  `context_window_exceeded`, `stop_sequence`. `end_turn`, `tool_use` and
  `unknown` say nothing.

### 11.3 Test traffic

Rows written by a test account (email matching chat-edge's
`TEST_ACCOUNT_EMAIL_PATTERN`, default `@local$`) carry `is_test = true`
on `identity.users`, `chat.conversations` and `chat.messages_masked`
(migration 045), set once at provisioning and inherited at insert. The
admin analytics exclude them unless asked (`include_test=1`).


## 12. Every turn leaves a row, and a row keeps what happened (FIX121)

### 12.1 A send that arrives twice is one turn

No model provider de-duplicates a request for us: their request ids exist
to find a request afterwards, not to refuse a repeat. The server that
writes the row is the only place this can be decided, and the key is the
id the turn already has (§11.1) — there is no second id.

An `X-Request-Id` the **client** chose (well-formed per §11.1) is the
idempotency key of `POST /conversations/{id}/messages`. An id chat-edge
minted is not a key, and such a send behaves exactly as it always did.

| The key in this conversation | Answer |
|---|---|
| never seen | The turn runs. The user row records the key as `run_id`. |
| seen, same `content` | `200`, header `Idempotent-Replayed: true`, and the **first** turn: a streaming caller is attached through §4 (recorded frames verbatim, then the live tail if the run is still going); a non-streaming caller gets the stored answer row. Nothing is generated, charged or written twice. |
| seen, same `content`, that turn **failed or was stopped** | `200`, `Idempotent-Replayed: true`, answered **from the row**: `message_start`, the text it kept, then its own terminal — `error` with the row's `error_code`, `cause` and `attempts`, or `cancelled`. Not through §4: a send refused before its stream opened has no recorded frames, and §4's snapshot would call it `GENERATION_INTERRUPTED`. The HTTP status of such a refusal is not stored, so the repeat carries the same **code** on a stream frame rather than the same status. |
| seen, different `content` | `409 IDEMPOTENCY_KEY_REUSED`. The key names one request. |
| seen, no answer row yet (two copies raced) | `409 REQUEST_IN_PROGRESS`; retry and get the replay. |

Two copies arriving in the same instant are arbitrated by the database
(migration 047: one native user row per `(conversation_id, run_id)`); the
loser is answered as the repeat it is. Imported desktop rows stay outside
that index — they are idempotent on `(conversation_id, client_id)`.

A client that wants this protection mints the id **before** the request
leaves, once per user action, and reuses it only to retry that same action.

### 12.2 The attempt log of a failed turn

The terminal `error` frame of a turn whose fallback walk was exhausted
carries `attempts` (one entry per model tried). chat-edge stores a bounded
copy on the row (`attempts`, migration 047: at most 12 entries, the keys
`provider model error_code error_message status attempted_at latency_ms`,
text cut at 300 characters) and returns it on the list wire as
`error_attempts` — the field the page already renders for the live frame,
so a reload, another device and the desktop all show the same list. The
desktop host forwards the frame's list to the page and sends it on the
imported item as `error_attempts`.

### 12.3 The import mirrors a turn that already happened

`POST /conversations/{id}/messages/import`:

* There is **no ceiling on a message's content**. The one size limit is on
  the request: 32 MiB, answered `413 REQUEST_TOO_LARGE`.
* An item that cannot be stored is **named, not fatal**: the response is
  `200 {imported, skipped, rejected: [{index, client_id, reason}]}` and its
  storable neighbours are stored. `rejected` is absent when there is
  nothing to report. The request is refused as a whole (`422`) only when
  it is malformed as a whole: empty, more than 200 items, or no storable
  item at all.
* A client that receives `rejected` (or any non-2xx) must tell the person
  the turn was not saved; a log line is not telling anyone.

## 13. A turn nobody will finish, and a restart that loses none (FIX131)

### 13.1 Interrupted is an ending

A turn whose process went away before it ended (a crash, a kill, a restart
that ran out of drain time) is written down as **interrupted**: status
`error`, `terminal_cause` `interrupted`, `error_code`
`GENERATION_INTERRUPTED`; the text it had streamed stays on the row. The
pages render it as "this answer did not finish" with Retry (regenerate) —
live and after a reload. It is never left `streaming`.

Who writes it:

* **the shutdown drain** (13.2) for the turns it had to cut; the live stream
  ends on `error` `GENERATION_INTERRUPTED` (`cause: interrupted`,
  `retryable: true`);
* **the interrupted-turn sweep** (chat-edge, at start-up and every
  `INTERRUPTED_TURN_SWEEP_INTERVAL`, default 60 s) for any other
  `streaming` row older than `INTERRUPTED_TURN_MIN_AGE` (2 min);
* **the resume path** for a row it is asked about and finds orphaned.

**Ownership.** Every streaming row holds a **run lease** (Redis
`chat:runlease:<message id>` = the instance id, TTL `RUN_LEASE_TTL` 45 s,
renewed every 15 s with `SET … EX`) while a process runs it. The sweep
settles a row only when no lease exists on two looks at least 30 s apart
(so a Redis that lost its keys does not close live turns before their
owners renew), and never when the lease check fails. The resume path
answers `409 RESUME_UNAVAILABLE` for a row another process leases.

### 13.2 The restart

On SIGTERM chat-edge keeps its listener open and:

1. refuses every **new** turn (send, regenerate, edit, a question's answer)
   with **`503 SERVER_DRAINING`** + `Retry-After` before anything is written
   (Retry-After is exposed to the cross-origin page);
2. lets the turns in flight finish, up to `CHAT_EDGE_DRAIN_TIMEOUT` (60 s);
3. cuts the rest as interrupted (13.1), settles any row whose ending was not
   written within `CHAT_EDGE_DRAIN_GRACE` (10 s), releases every lease;
4. closes the HTTP server and exits. `stop_grace_period` is 90 s.

The web page re-sends a refused request by itself — the same `X-Request-Id`
on a send, so a copy that did land is replayed (12.1), never run twice —
backing off 2 s → 10 s for up to 120 s, and treats a refused connection
right after a `SERVER_DRAINING` as the same restart. When the wait runs out
nothing was sent: the rows go and the text is back in the composer.
