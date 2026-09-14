# Data Retention & Deletion

This document describes what the system DOES: which stores hold user
data, how retention windows are configured, when deletion runs, what
evidence every deletion leaves behind, and where the boundaries are.
It is written to be handed to an enterprise buyer's procurement or
security review as-is.

**Deployment note.** This product is self-hosted: the data lives in
the customer's own infrastructure, not ours. Every statement below is
therefore a statement about what the SYSTEM enables the customer's
administrators to do — configure windows, delete on schedule, and
prove it from the audit log — not a promise by the vendor about data
the vendor does not hold. The audit log is the deliverable an external
auditor should be pointed at.

## The commitments, as numbers

| Property | Value |
|---|---|
| Retention window | Admin-configurable per tenant; **minimum 30 days**; empty = keep forever |
| Deletion time | **00:00 UTC daily** — a fixed hour, not "within 24 hours"; restarts do not move it |
| User-deleted chats | Removed from the user's view **immediately**; permanently erased from the server after the configured purge window (default **30 days**) |
| Deletion semantics | **Permanent and unrecoverable** — hard delete, not a hidden flag |
| Evidence | Every deletion and every retention-setting change writes an audit row that **no deletion path can remove** |
| Settings changes | Take effect **at the next scheduled run**, never retroactively or immediately; the admin UI shows the exact effective time |
| Audit retention | **365 days**, configurable — and evidence still outlives the data it describes, because a deletion's audit row is written at the moment of deletion and then keeps its own full year |

### Why the 30-day floor

A shorter window turns retention into an accidental kill switch: one
mistyped setting could erase a tenant's entire recent history at the
next midnight run. Thirty days keeps a full monthly working cycle
recoverable-by-existence while a misconfiguration is noticed, and
matches the floor common in enterprise chat products. The floor is
enforced three times — API validation, a database CHECK constraint,
and the sweep itself refuses shorter windows — so no single bypass
can undercut it.

### The deletion timetable, by example

Tenant policy: retention 90 days, user-delete purge window 30 days.

| Event | What happens, when |
|---|---|
| A conversation's last activity is 2026-06-01 | It is erased at **00:00 UTC on the first nightly run after 2026-08-30** (90 days later) |
| A user deletes a chat on 2026-09-03 | Gone from their screens immediately; permanently erased at **00:00 UTC on the first run after 2026-10-03** |
| An admin lowers retention from 180 to 90 days at 14:00 | Nothing happens at 14:00. The change applies from **the next 00:00 UTC run**, and the UI said so when they saved |
| An admin enables retention for the first time | The first nightly pass runs in **dry-run**: it counts and audits what WOULD be deleted, deletes nothing, and a human turns dry-run off after reading the numbers |

### Exceptions — when the system will NOT delete

A deletion system with no stated exceptions is making a promise it
cannot keep. These are the cases where data outlives its window, and
each is visible rather than silent:

* **Legal hold / investigation.** The mechanism is the per-tenant
  enable switch: an administrator under a preservation obligation
  turns the tenant's sweep off (or keeps dry-run on), and that change
  is itself an audited event with before/after values — the hold
  leaves the same quality of evidence as a deletion would have.
* **Deletion-audit rows.** The evidence of deletions deliberately
  outlives the deleted data (see audit retention above). Audit rows
  contain identifiers and counts, never content.
* **Content-free accounting.** Token/cost usage rows and job-outcome
  records (no message content) are retained for billing and
  operations and are outside conversation erasure by design.
* **Client-local caches** are the user's own copies on their own
  machine. The desktop app sweeps its conversation-state cache after
  **30 days by default** and provides an immediate wipe command, but
  the server cannot reach into a client device.

---

## Where a message lives, and which mechanism erases it

A single chat message can exist in these places. Every one has a
named owner mechanism — or an explicit statement that it is out of
reach and why.

| Store | Content? | Erased by |
|---|---|---|
| `chat.messages_masked` (the transcript) | yes | conversation erasure cascade |
| `chat.conversation_states` (structured summaries) | yes | cascade |
| `chat.response_context_sources` (evidence excerpts) | yes | cascade |
| `chat.canvas_artifacts` | yes | cascade |
| `chat.conversation_files` + storage objects | yes | cascade (objects first, then rows) |
| `chat.shared_conversations` (share links) | links to messages | cascade |
| `chat.cloud_scheduled_tasks` (task prompts) | yes | cascade |
| `chat.feedback` | rating only | cascade (via the conversation's messages) |
| `chat.user_memories` / `memory_events` (derived facts) | yes | the user-scoped memory purge (`DELETE /account/purge-memories`) — deliberately separate: a memory may be derived from many conversations, so erasing one conversation must not silently delete facts the user still relies on |
| Gateway `request_logs` (masked snapshots; raw columns) | raw columns for ≤ the DLP window (90 days), masked snapshots after | time-based: the nightly strip NULLs raw columns; archive + expiry + partition drops age the rows. **Not conversation-addressable** — these rows are keyed by request, not conversation, so per-conversation erasure cannot select them; their lifetime is bounded by the time-based windows instead |
| `request_logs_archive` (cold storage) | masked only (the pipeline refuses to move rows on a night the strip failed) | the archive-expiry step |
| Desktop local cache (`history/*.json` transcripts with tool results, `conversation-state/*.json`, run ledger, logs) | yes (plaintext) | TTL sweep at launch — `localStateTtlDays`, **30 days by default** (0 disables) — plus the app's immediate **Clear local data** command. Server-side erasure cannot reach a client disk — stated here rather than implied otherwise |
| Streaming resume ledger (Redis) | yes | self-expiring TTL (~10 minutes) |
| Memory export files | yes | 24-hour link expiry + deleted by the memory purge |
| `chat.cloud_task_runs`, `chat.compaction_usage` | no (counts/outcomes) | retained — content-free accounting (see exceptions). **Label caveat (FIX113):** `compaction_usage.trigger` rows written between FIX111 (`d1a4223`, when the desktop began compacting through `POST /compact`) and the FIX113 Part B commit say `manual` for the desktop's AUTOMATIC compactions as well — the endpoint could not say otherwise. From FIX113 Part B the desktop sends `auto` and the button `manual`; the earlier rows are left as written, not corrected, and a per-trigger reconciliation over that window must not read `manual` as "a person pressed the button" |

A structural guard keeps this table honest: a coverage test derives
the list of conversation-linked tables from the database schema
itself, and fails the build if a new table appears that is neither in
the erasure cascade nor in the documented keep-list.

## The mechanisms

### Conversation erasure cascade (chat service)

One transaction erases the conversation row and every content-bearing
dependent, and commits the deletion-audit row **in that same
transaction** — "deleted but unrecorded" and "recorded but not
deleted" are both unrepresentable. File storage objects are removed
first (their rows carry the paths). Triggered by two rules:

* `retention_expired` — no activity for `transcript_retention_days`
  (applies to archived and pinned conversations too: the window is a
  compliance control, not a preference);
* `user_request` — the user deleted the chat; the sweep completes
  the erasure `deleted_conversation_purge_days` later.

Per-tenant settings live in `chat.tenant_retention_settings`
(`transcript_retention_enabled` default **false**, `dry_run` default
**true**, floor 30). The sweep runs at 00:00 UTC, caps each tenant's
nightly work, and refuses windows below the floor even if a row
somehow carries one.

### Nightly retention pipeline (gateway)

One ordered task at 00:00 UTC: **strip → archive → archive-expiry →
audit-aging**. The DLP raw-stripper always runs; the mover
(`log_archive_enabled`), the archive expiry
(`archive_expiry_enabled` + `archive_retention_days`) and the
audit-log aging (`audit_log_aging_enabled` +
`audit_log_retention_days`) are each off by default and dry-run
first. The mover is skipped on any night the strip failed, so
un-stripped rows can never reach cold storage; the order is enforced
in code and pinned by tests. Live expiry and aging passes commit
their own audit record atomically with the delete.

### Deletion audit

Two audit stores, one per service, neither reachable by any cascade:

* `chat.deletion_audit` — every conversation erasure (with per-table
  counts), memory purge, file deletion, user soft-delete, dry-run
  report, and retention-setting change (before/after values). Rows
  carry actor, action, scope, resource id, rule, counts, timestamp —
  **never deleted content**.
* the gateway's admin audit log — retention-pipeline deletions
  (archive expiry, audit aging) with counts and windows.

The audit trail's own aging runs through the same pipeline, records
itself, and its window must exceed every data window.

### Admin surface

`Data Retention` in the admin UI (backed by shared-secret admin
endpoints on the chat service): per-tenant settings with the floor
validated and every change audited and answered with its effective
time; a **preview** that reports exactly what the next live pass
would delete without deleting anything; and the deletion-audit
listing. Disabling or shortening retention takes effect on the
schedule, like every other change.

### Desktop client

`Clear local data` (tray menu, confirmed) removes the local
transcript cache (`history/`, the per-conversation chain with tool
results), the conversation-state cache, run ledger and logs — counts
reported, contents never. A TTL sweep at launch (`localStateTtlDays`,
**default 30 days**; 0 disables it) removes history and
conversation-state files that have not been touched inside the window.
A swept transcript costs the next desktop turn only its tool results:
the turn reads the server transcript instead. Until FIX109 neither
mechanism touched `history/`.

**Why a TTL and not encryption-at-rest.** The cache is written in
plaintext, and the answer to that is to stop keeping it rather than to
protect it in place. Two reasons. The threat encryption-at-rest would
address here — someone with the powered-off disk — is already covered
by the operating system's full-disk encryption, which every managed
deployment turns on; adding a second key for these files moves the
problem to key storage on the same machine without removing the data.
And the cache is a convenience, not the record: the conversation
itself lives on the server, so a file that expires costs the user
nothing but a slower reopen. Deleting data beats guarding it.

This is also why this one sweep ships ON while every server-side
deleting job ships off. On a server, "off" means the data sits in a
database the operator controls and can account for. On a laptop, "off"
means transcripts accumulate in plaintext indefinitely on a device the
operator cannot reach.

---

## Operating checklist for administrators

1. Enable retention for a tenant with `dry_run` **on** (the default).
2. After the next 00:00 UTC pass — or immediately via **Preview** —
   read the counts.
3. Turn `dry_run` off. Deletion begins at the following 00:00 UTC
   run; every erasure is in the deletion audit.
4. For a legal hold: disable the tenant's sweep; the change is
   audited with before/after values.

## Rollback & safety properties

* Every deleting mechanism ships **off**, previews before it acts,
  and is enabled per tenant / per flag by a human.
* Schema changes are additive (new tables only); reverting them drops
  only the new, initially-empty tables.
* The erasure cascade is transactional per conversation — a failure
  rolls the whole conversation's erasure back, never leaving a
  half-erased thread.
* Counts, never contents: no dry-run report, log line, or audit row
  contains message text or raw detector matches.
