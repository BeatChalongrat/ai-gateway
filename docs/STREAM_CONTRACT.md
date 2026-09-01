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
