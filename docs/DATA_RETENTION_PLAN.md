# Data Retention & Deletion Plan

Status: **plan, not yet implemented** (except item 2's stop-gap, which is live).
Scope: every store the product writes user data into — chat transcripts, request
logs and their archive, deletion audit trails, file attachments, desktop-local
state, and the backend retention machinery itself.

The memory subsystem is the reference implementation this plan measures
everything else against: per-tenant `retention_days` with a daily enforcer
(`services/chat-edge-api/internal/service/retention_enforcer.go`), gated JSON
export with expiring download links (`internal/service/export_service.go`), and
a hard-delete purge behind an explicit confirmation string
(`internal/service/purge_service.go`). Every other store currently falls short
of that bar in at least one way.

## Principles

1. **Every path data flows in must have a path out.** A table that can only be
   written is a liability, not storage. Today `chat.messages_masked` and
   `request_logs_archive` are write-only.
2. **Evidence of deletion must survive the deletion.** A purge that deletes its
   own audit trail (item 3) produces the opposite of what an auditor asks for:
   "prove this user's data was erased" must be answerable *after* the erasure.
3. **An invariant that lives in a comment must live in a test.** The archive
   job's config comment asserted that matching two 90-day windows kept raw
   content out of cold storage; nothing ordered the two jobs, so the assertion
   held only by luck. `backend/tests/test_log_archive_gate.py` and
   `backend/tests/test_partition_drop_guard.py` are the pattern: the guard is
   executed, not described.
4. **Counts and shapes, never contents.** Any report, log line, or metric
   produced by retention machinery may say how many rows and which detectors —
   never the matched text or raw payloads themselves.

---

## 1. Chat transcripts (`chat.messages_masked`) — no delete path at all

**Current state.** Conversation deletion is a soft-delete
(`internal/service/conversation_service.go:336-351` sets
`is_deleted`/`deleted_at`; `internal/repository/conversation_repo.go:650-667`).
`internal/repository/message_repo.go` has no delete function of any kind, and
`PurgeService` does not touch messages. Message content is therefore retained
indefinitely for every user, including users who invoked the memory purge.
Clients that sync see `410 Gone` and drop their local copy
(`internal/handler/import.go:115`), which hides — but does not remove — the
server rows.

**Target.** Per-tenant transcript retention (same knob shape as
`tenant_memory_settings.retention_days`) plus inclusion of transcripts in the
right-to-erasure cascade, with a defined answer to "what survives for audit"
(recommendation: a tombstone row carrying conversation id, counts, timestamps,
and the deletion event — never content).

**Options.**

| Option | Pros | Cons |
|---|---|---|
| a. Hard-delete rows on purge + retention sweep | Honest erasure; smallest schema change | Loses the "messages remain for audit" property the soft-delete comment claims; needs a tombstone design to keep principle 2 |
| b. Scrub-in-place (NULL content columns, keep rows) like the memory enforcer | Preserves row-level audit shape; mirrors an existing pattern | Table keeps growing; "deleted" rows still exist and must be excluded everywhere |
| c. Partition `messages_masked` by month and drop whole partitions | Cheap bulk deletion at scale | Retention becomes global-per-partition, not per-tenant; large migration on a hot table |

**Effort:** L (new repo methods + cascade wiring + retention sweep + the
audit-survival decision + migration). **Destructive if done wrong:** yes — a
cascade bug here deletes primary user content; build behind a flag, verify on
a copy first.

## 2. `request_logs_archive` — write-only cold storage, unordered against the raw-stripper

**Current state.** The daily mover (`backend/app/jobs/log_archive_job.py`)
copies `request_logs` rows older than `log_archive_days` into
`request_logs_archive` and deletes them from the hot table. The archive has no
expiry and no delete path by design (`log_archive_job.py:13-15`). The DLP
raw-retention job (`backend/app/jobs/dlp_retention.py`) NULLs raw columns and
strips finding text on the *hot* table only. Both are independent 24-hour
loops started in `backend/app/main.py` with no ordering between them, so a day
where the mover fires first copies un-stripped rows into a table they can
never leave.

**Stop-gap (live now).** `log_archive_enabled` defaults to false
(`backend/app/core/config.py`); when off the mover task is never created
(`backend/app/main.py`, pinned by `backend/tests/test_log_archive_gate.py`).
The stripper keeps running unconditionally. Measured before stopping: the
archive holds 3,088 rows (2026-05-18 → 2026-06-01, one tenant) and **zero** of
them contain `input_raw`, `input_raw_encrypted`, `output_raw`, `output_final`,
or any finding text (entries carry only `matched_text_len` integers) — the
exposure is structural, not realized. Partition maintenance
(`backend/app/jobs/partition_maintenance_job.py`) is verified to refuse
dropping non-empty partitions (`backend/tests/test_partition_drop_guard.py`),
so the hot table simply grows while the mover is off — recoverable, unlike a
raw row in permanent cold storage.

**Target.** Either the archive earns a full lifecycle, or it is retired.

**Options.**

| Option | Pros | Cons |
|---|---|---|
| a. Strip-before-move: the mover refuses any row the stripper has not visited (or strips inline during the copy), plus an archive expiry job | Keeps cold storage; enforces the intended invariant in code | Two new pieces (gate + expiry); the "archive never deletes" assumption in the job's docstring and dashboards must change |
| b. Retire the archive: keep everything in `request_logs`, rely on partitioning + partition drops for aging | One store, one lifecycle; partition machinery already exists and is guard-tested | Loses the hot/cold split; per-tenant retention still needs the partition-vs-tenant answer from item 1c |
| c. Re-enable as-is with a fixed job ordering (stripper runs, then mover, same scheduler slot) | Smallest diff | Ordering by scheduling is fragile (restarts, slow runs); still no expiry, so the archive remains write-only |

**Recommended:** (a), with the refusal gate tested the same way the schema
drift guard is. **Effort:** M. **Destructive if done wrong:** the expiry job
deletes archive rows — it must land only after the strip-gate is proven,
never before.

## 3. Deletion audit — the purge deletes its own evidence

**Current state.** `PurgeService.PurgeAllUserMemories`
(`internal/service/purge_service.go:80-163`) hard-deletes
`chat.memory_events` along with the memories
(`internal/repository/memory_repo.go:389-398`) — the only in-database record
that the purge happened is removed by the purge. What remains is one
process-log line with counts (`purge_service.go:149-161`). Conversation
soft-deletes and file deletes write no audit row anywhere. The backend has a
tamper-evident, hash-chained audit table
(`backend/app/models/admin_audit_log.py`,
`backend/app/services/audit_integrity.py`) — chat-edge never writes to it.

**Target.** Every destructive operation (memory purge, transcript delete, file
delete, future erasure cascades) writes one durable audit row — actor, scope,
counts, timestamp, never content — to a store the operation itself cannot
delete. Purge keeps deleting `memory_events` content rows but writes a purge
record elsewhere first.

**Options.**

| Option | Pros | Cons |
|---|---|---|
| a. chat-edge writes to its own `chat.deletion_audit` table excluded from every purge scope | Small, local; no cross-service call on the delete path | Second audit system next to the backend's chained one; no tamper-evidence unless the chain logic is ported |
| b. chat-edge calls an internal backend endpoint that appends to the existing hash-chained `admin_audit_log` | One audit spine, tamper-evident, already dashboarded | Adds a service dependency to the delete path (must fail open on audit-write failure or deletes break during backend outages — decide which) |

**Effort:** M either way. **Order note:** this should land *before* item 1's
cascade — new deletion machinery should be born audited.

## 4. File attachments — outside every cascade

**Current state.** Per-file hard delete works end to end
(`internal/service/file_service.go:931-946` deletes the DB row and the
object via `internal/storage/local.go` / `internal/storage/s3.go`), but it is
only reachable one file at a time by explicit user action. Conversation
deletion does not cascade to files; the memory purge does not touch them; no
storage lifecycle rule exists in the compose files, so orphaned objects
persist indefinitely.

**Target.** Files ride every deletion their conversation rides: conversation
delete (or its retention sweep) removes its files; the erasure cascade removes
all of a user's files; a periodic orphan sweep reconciles storage against the
DB.

**Effort:** S for wiring `FileService.Delete` into the purge cascade; M
including the orphan sweep and a storage lifecycle policy. **Destructive if
done wrong:** object deletion is irreversible; the sweep must run in
report-only mode first.

## 5. Desktop-local state — plaintext, no wipe

**Current state.** The desktop shell persists per-conversation structured
summaries as plaintext JSON under the user profile
(`apps/desktop/src/main/conversation-state.ts`), plus shell logs and window
state. Only the auth session file is OS-encrypted and cleared on logout
(`apps/desktop/src/main/session-store.ts:50-56`). The per-turn ledger expires
completed runs after 10 minutes (`apps/desktop/src/main/run-ledger.ts:47`),
but `conversation-state/*.json` has no TTL, and there is no "clear local
data" action anywhere in the app — only uninstalling removes the files.

**Target.** (i) a local wipe command (menu + IPC) that removes
conversation-state, logs, and ledger in one action; (ii) logout optionally
implies wipe (policy-controlled); (iii) a TTL sweep for conversation-state
files whose conversation is gone server-side; (iv) the standing
encryption-at-rest question for these files decided once, for all of them.

**Effort:** M (wipe + TTL are small; the encryption decision drives the real
cost). **Destructive if done wrong:** low — everything local is
re-derivable from the server.

## 6. Backend `RetentionService` — implemented, tested, never scheduled

**Current state.** `backend/app/services/retention_service.py` implements
hard-delete cleanup for `request_logs` and `admin_audit_log` with its own
defaults, has passing tests, and is referenced from no production code path —
`backend/app/main.py` schedules the DLP stripper, the (now gated) mover, and
partition maintenance, but never this service. Two overlapping retention
mechanisms exist for the same table; only one runs.

**Target.** One mechanism per store. Either fold anything this service does
uniquely (audit-log aging) into the scheduled jobs and delete the service, or
schedule it deliberately and retire the overlap.

**Effort:** S. **Note:** its `cleanup_audit_logs` (365-day aging of
`admin_audit_log`) is the only audit-trail aging anywhere — whatever fate the
service has, that policy needs an owner, and it interacts with item 3
(deletion evidence must outlive the data it describes; an audit row aged out
after one year may be too short for erasure evidence).

---

## Suggested order

| Step | Item | Why this order |
|---|---|---|
| 1 | 3 — durable deletion audit | Everything after this should be born audited |
| 2 | 2a — strip-gate + archive expiry (or 2b retire) | Unblocks re-enabling the mover; smallest risk once audited |
| 3 | 4 — files into the cascade | Small, mechanical, closes an obvious orphan hole |
| 4 | 1 — transcript retention + erasure cascade | The big one; depends on the audit spine and the tombstone decision |
| 5 | 5 — desktop wipe + TTL | Independent; schedule opportunistically |
| 6 | 6 — resolve `RetentionService` | Do alongside step 2 (same files, same review) |

## Migration & data-destruction checklist

- Items 1, 2, and 4 delete user data when they work *correctly* — each needs:
  a feature flag defaulting off, a report-only dry-run mode that prints counts
  only, and a rehearsal against a restored copy before first production run.
- Item 2's expiry must not ship before its strip-gate: expiring an archive
  that still accepted raw rows would destroy the evidence of what leaked.
- Item 1's schema change (tombstones or scrub columns) needs a migration on
  the largest table in the chat database; plan for batched backfill.
- Nothing in this plan modifies DLP detection or masking behavior; retention
  machinery consumes what the DLP pipeline already wrote.
