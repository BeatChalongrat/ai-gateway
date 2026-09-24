# The user owns their data (FIX132 C)

What a user can do with their own data themselves, what is deleted when,
what is kept and why. The mechanisms behind the numbers are in
[DATA_RETENTION_PLAN.md](../DATA_RETENTION_PLAN.md).

## At a glance

| The user… | What happens at once | What happens later |
|---|---|---|
| deletes a chat | It disappears from every screen and every reader (messages, files, search, share links). | **30 days** later (`deleted_conversation_purge_days`) the nightly chat-edge sweep (00:00 UTC) erases the chat, its messages, files **and the file bytes**, summaries, canvases, share links, task prompts. Backups keep a copy until they roll off (**14 days** for the local backups). |
| deletes their account (Settings → ข้อมูลของคุณ → ลบบัญชีของฉัน, typing their email) | Every chat hidden; every session ended on every device (token_version bump + chat-edge revocation); sign-in refused on the password **and** the Google/GitHub door; API keys revoked; scheduled tasks stopped. | **30 days** later (`ACCOUNT_PURGE_DAYS`) the backend's nightly pipeline (00:00 UTC, step 5) has chat-edge erase everything the user owns there, then turns the portal row into an anonymised tombstone (below). Backups roll off after that. |
| exports all their data (Settings → ข้อมูลของคุณ → ส่งออก) | A ZIP is built in the background (status pending → ready / failed). | The download link works for **24 hours**; the hourly export sweep then deletes the file (row kept as `expired`, ids and sizes only). |

The Settings page and both delete-chat confirmations read the period from
`GET /api/v1/account/data-policy`, so the words the user reads are the
numbers the sweep uses. If a tenant turns the purge off, the UI says the
chat is hidden and kept until an administrator removes it — it never
promises a removal that will not happen.

## The switches

| Where | Name | Default | Effect |
|---|---|---|---|
| chat-edge env | `DELETED_DATA_PURGE_MODE` | `on` | Ceiling for the deleted-chat purge, every tenant: `on`, `dry_run` (count + audit, delete nothing), `off`. |
| chat.tenant_retention_settings (migration 050) | `deleted_purge_enabled` / `deleted_purge_dry_run` | `true` / `false` (also with no row) | A tenant can lower the ceiling (off / dry-run). An admin retention edit that does not name them keeps them. |
| chat.tenant_retention_settings | `deleted_conversation_purge_days` | 30 | The window. |
| chat-edge env | `BACKUP_RETENTION_DAYS` | 14 | Deletes nothing — it is what the UI says about backups. **Change it if off-site backups are turned on** (see "Backups"). |
| chat-edge env | `EXPORT_MAX_FILE_BYTES` / `EXPORT_MAX_CONVERSATIONS` | 500 MB / 5000 | Caps of one export; files past the cap are listed in the ZIP's README instead. Five exports per user per 24 h. |
| backend env | `ACCOUNT_PURGE_MODE` | `on` | `on`, `dry_run`, `off` for the account purge. |
| backend env | `ACCOUNT_PURGE_DAYS` | 30 | The account window. |

The inactive-transcript rule (`transcript_retention_enabled`, `dry_run`)
is unchanged: per-tenant opt-in, dry-run first.

## What the export contains

`sranchat-export-YYYY-MM-DD.zip`:

* `chat.html` — every live chat (sidebar and archive), readable in any
  browser, UTF-8 with Thai fonts; message text is escaped, attachments
  link into `files/`.
* `conversations.json` — the same chats as data. Messages are the
  **active branch** (what the chat shows), in the form the chat shows
  them: the stored display text, or the owner's reload representation
  when the DLP display lifecycle is on. Masked text stays masked.
* `files/` — uploaded and generated file bytes (including uploads that
  were never sent), up to the cap.
* `memories.json` (not deleted ones), `projects.json` (name,
  instructions, chat count), `settings.json` (custom instructions,
  skills), `README.txt` (Thai + English: what is and is not included).

Not included, and said so in the README: chats the user deleted (inside
their 30-day window), older versions of edited messages, team knowledge
bases (they belong to the team), feedback ratings, share links, usage and
credit records (the Portal shows those). Only the caller's own rows are
read; every query is scoped to their tenant and user id. The export
covers the tenant the user is signed in to.

## What the account purge removes, and what it keeps

chat-edge (`POST /internal/accounts/{id}/purge`, one audit row
`account_purged` with counts):

* every conversation of the user, any tenant, through the existing
  erasure cascade (file bytes first);
* `feedback`, `canvas_artifacts`, `response_context_sources`,
  `conversation_states`, `pending_questions`, `cloud_task_runs`,
  `cloud_scheduled_tasks`, `conversation_files` (+ bytes),
  `memory_events`, `user_memories`, `projects`, `folders`,
  `user_preferences`, `user_skills`, `data_export_requests` (+ files),
  `identity.refresh_tokens`, `identity.invitation_tokens`,
  `identity.users`; the workspace name chat-edge derived from the email.
* **kept:** `chat.deletion_audit` (the evidence: ids, kinds, counts) and
  `chat.compaction_usage` (token/cost accounting, content-free). A
  coverage test fails when a new table with a user column is in neither
  list.

backend (`anonymise_portal_user`, admin audit row
`portal_user.account_purged` with counts):

* deleted: `connector_tokens` (OAuth tokens), `generation_jobs`
  (prompts), `project_members`, the avatar file;
* `request_logs` / `request_logs_archive`: `user_id` cleared and every
  text column (raw, masked, output, DLP findings, feedback comment)
  emptied — rows reached by `user_id` and, for rows older than that
  column, through the credit ledger's request ids. Token counts and cost
  stay for the usage totals;
* `api_keys` revoked and detached, `projects` detached;
* **kept: `credit_transactions`** — financial records (amount, balance,
  type, time) a company must keep; they point at the tombstone;
* `portal_users` becomes a tombstone: email
  `deleted-<id>@deleted.invalid`, no password, name, avatar or OAuth
  link, `status = 'deleted'`, `deleted_at`. The email is free for a new
  sign-up. The team name signup derived from the email goes when nobody
  else is in that team.

Refused, with a Thai reason: the only owner of a team that still has
other members (they must remove the members or have an administrator
name a new owner first).

## Order and failure

1. Request: the backend checks the typed email and the team rule, then
   calls chat-edge `deletion-request`. If chat-edge does not confirm,
   **nothing changes** and the user is told to try again (503). Only
   then is the account marked.
2. Purge: chat-edge first; if it does not confirm, the portal row stays
   `pending_deletion` and the next night tries again. Both calls are
   idempotent.

Both internal calls use `CHAT_EDGE_INTERNAL_URL` +
`X-Internal-Service-Secret` (the `/internal/auth/revoke` pattern).

## Backups

Nightly backups (03:15) copy both databases and the uploads volume.
The local copy keeps **14 days** (`BACKUP_KEEP_DAYS`), so a purged chat
or account can be restored from a backup for up to 14 days after the
purge — the UI says so. **If off-site backups are configured**
(`BACKUP_OFFSITE_*`, restic: 14 daily, 8 weekly, 12 monthly snapshots),
deleted data survives in the weekly/monthly snapshots for up to a year:
set `BACKUP_RETENTION_DAYS` to the real figure (e.g. 365) so the UI stays
true, or shorten the off-site retention. A restore from backup must be
followed by re-running the purges (they are idempotent) so nothing the
user deleted comes back.

## Also time-bound, not purged by id

* Redis: the stream-resume ledger (~10 min), plan outcomes (30 days),
  memory-extractor watermarks, token revocation marks (24 h).
* Desktop local caches (30-day TTL + "Clear local data").

## Where we differ from ChatGPT / Claude

* Export is one ZIP with `chat.html` + `conversations.json` like
  ChatGPT's; we add the file bytes (ChatGPT's export links to them) —
  the user asked to own their files too.
* Deleted accounts: vendors also wait ~30 days; we keep the credit
  ledger anonymised because billing records must outlive the account,
  and keep deletion-audit ids so the erasure can be proven.
* The only-owner rule: vendors hand an organisation to another admin;
  our portal cannot transfer ownership yet, so we block and say why.
