# Backups (FIX129 A5)

Before FIX129 there was no scheduled backup at all, and `scripts/backup-db.sh`
(now a retired wrapper) dumped only the gateway database as plain SQL, never
restored it, and nothing ran it. The 2026-09-22 incident (NIGHT2: the backend
wedged 34 minutes on one abandoned `FOR UPDATE` transaction) was the reminder
that a database that is never backed up is one bad transaction from gone.

The backup is now a compose service: `backup` in `docker-compose.webchat.yml`,
built from `infra/backup/`. It runs inside the stack, on the shared
`ai-gateway_default` network, and reaches both database servers by service
name.

## Schedule, location, retention

| What | Value | Where to change |
|---|---|---|
| Schedule | daily at `BACKUP_AT` = **03:15** in `TZ` = **Asia/Bangkok** | `.env`: `BACKUP_AT=HH:MM`, `TZ=` |
| Location | `BACKUP_DIR` on the host, default **`../ai-gateway-backups/daily`** (a sibling of the checkout, so a re-clone, `git clean` or worktree removal never eats it) → `/backups` in the container | `.env`: `BACKUP_DIR=/mnt/backups/sran` (put it on another disk on a real deployment) |
| Retention | directories older than `BACKUP_KEEP_DAYS` = **14** days are deleted at the end of each run | `.env`: `BACKUP_KEEP_DAYS=` |
| Success note | silent by default; `BACKUP_NOTIFY_SUCCESS=1` sends a quiet daily summary | `.env` |

The service runs with `restart: unless-stopped`, so it comes back with the
stack. One backup directory per run: `<BACKUP_DIR>/<YYYYMMDD_HHMMSS>/`. A run
that fails leaves `<YYYYMMDD_HHMMSS>.failed/` (whatever was written) so the
partial output can be inspected; it is pruned like the others.

## What is in a backup

```
20260923_031500/
  gateway.dump      pg_dump -Fc of the gateway DB    postgres / ai_gateway   (tenants, users, keys, request_logs, credits, models …)
  sran_ai.dump      pg_dump -Fc of the chat DB       postgres-webchat / sran_ai   (chat.* — conversations, messages, files, memories …)
  uploads.tar.gz    the chat_edge_uploads volume     uploaded + generated files (/app/data/uploads in chat-edge)
  manifest.json     sizes + sha256 of the three files, row counts of the key tables, versions, timing
  drill.json        the restore drill's verdict (written by the drill that follows every backup)
```

Row counts in the manifest are exact for the dump: each database is counted
and dumped inside ONE exported snapshot (`pg_export_snapshot` +
`pg_dump --snapshot`), so a live server taking writes during the dump does
not make the numbers drift. Key tables:

* gateway: `tenants`, `portal_users`, `request_logs`, `credit_transactions`, `model_configs`
* chat: `chat.conversations`, `chat.messages_masked`, `chat.conversation_files`

Not in a backup: Redis (rate-limit windows, the resumable stream ledger, the
DLP token vault — all rebuildable or short-lived), Prometheus/Grafana data, the
`sran_chat` database on the base `postgres` (the profile-gated desktop
shape's chat-edge; the live chat store is `sran_ai` on `postgres-webchat`),
and MinIO (`STORAGE_BACKEND=s3` deployments must back the bucket up
separately — the uploads tar covers the `local` backend only).

## The drill: every backup is restored

A backup that was never restored is a hope. After every run the service
restores each dump into a scratch database on the same server
(`ai_gateway_drill`, `sran_ai_drill`; `createdb --template=template0` then
`pg_restore --no-owner --no-privileges`), counts the same key tables, and
compares them with the manifest — exactly. It re-hashes the three files and
lists the tar. The verdict is `drill.json`:

```json
{ "pass": true, "checks_total": 14, "checks_failed": 0,
  "checks": [ {"name": "sha256:gateway.dump", "pass": true, "expected": "…", "actual": "…"},
              {"name": "restore:gw",          "pass": true, "expected": "pg_restore rc=0", "actual": "rc=0"},
              {"name": "rows:gw:request_logs","pass": true, "expected": "48213", "actual": "48213"},
              {"name": "rows:chat:chat.messages_masked", "pass": true, "expected": "1312", "actual": "1312"},
              {"name": "uploads:entries",     "pass": true, "expected": "57", "actual": "57"} … ] }
```

The scratch databases are dropped in an EXIT trap, so a crashed drill never
leaves them behind. A drill that fails raises the alert (below) and makes
`run --rm backup now` exit 2.

## Run one now

```sh
docker compose -p ai-gateway -f docker-compose.webchat.yml run --rm backup now
```

That is backup + drill + notification, then exit (0 = written and proven;
1 = the backup failed; 2 = the drill failed). Sub-commands, when you need
one piece:

```sh
docker compose -p ai-gateway -f docker-compose.webchat.yml run --rm backup backup            # dump only
docker compose -p ai-gateway -f docker-compose.webchat.yml run --rm backup drill /backups/20260923_031500
docker compose -p ai-gateway -f docker-compose.webchat.yml run --rm backup notify info "test" "hello from the backup service"
```

The scheduled container's log (`docker logs ai-gateway-backup`) has one
timestamped line per step: the snapshot id, each table's count, each file's
size, the drill's PASS/FAIL per check, the prune, and the summary. The same
lines come out of `run --rm … now`.

`scripts/backup-db.sh` is retired and now only runs the command above.

## Restore for real

Read all of it before the first command. The drill has already proven, on
the night's backup, that steps 3–4 work against these exact files; the
difference here is the target is the live database.

1. **Pick the backup and read its verdict.** Choose the newest
   `<BACKUP_DIR>/<stamp>/` whose `drill.json` says `"pass": true`. Note the
   `manifest.json` counts — they are what you will have afterwards.
   Everything written after that stamp is lost; say so to whoever needs to know.

2. **Stop the writers, keep the servers.** The database containers stay up;
   the services that write must not.
   ```sh
   docker compose -p ai-gateway -f docker-compose.webchat.yml stop chat-edge-api web-chat backup
   docker compose -p ai-gateway stop backend gateway admin-ui portal log-worker mcp-gateway
   ```
   (Take one more backup of the current state first if the servers still
   answer: `run --rm backup backup`. It costs a minute and keeps the option
   to change your mind.)

3. **Restore the gateway database** (server `postgres`, db `ai_gateway`).
   Restore into a fresh database and swap names, so the broken one is kept
   until you are sure. From the checkout, with `.env` loaded
   (`set -a; . ./.env; set +a`):
   ```sh
   docker compose -p ai-gateway -f docker-compose.webchat.yml run --rm --entrypoint bash backup
   # inside the container, /backups is mounted:
   export PGPASSWORD="$POSTGRES_PASSWORD"
   createdb -h postgres -U "$POSTGRES_USER" --template=template0 ai_gateway_restore
   pg_restore --no-owner --no-privileges -h postgres -U "$POSTGRES_USER" -d ai_gateway_restore /backups/<stamp>/gateway.dump
   psql -h postgres -U "$POSTGRES_USER" -d ai_gateway_restore -c "select count(*) from request_logs"   # = manifest.gateway.counts.request_logs
   psql -h postgres -U "$POSTGRES_USER" -d postgres -c "alter database ai_gateway rename to ai_gateway_broken_$(date +%Y%m%d)" \
                                                     -c "alter database ai_gateway_restore rename to ai_gateway"
   ```
   The rename fails with "being accessed by other users" while anything is
   still connected — that is step 2 not being complete, not a reason to
   `--force` anything.

4. **Restore the chat database** the same way (server `postgres-webchat`,
   db `sran_ai`, user `chatedge`, password `$WEBCHAT_POSTGRES_PASSWORD`,
   file `sran_ai.dump`, scratch `sran_ai_restore`; the `vector` extension is
   created by the dump — pgvector is installed on that server).

5. **Restore the uploads** into the volume (only if the files are gone or
   wrong; the tar is the whole volume at backup time):
   ```sh
   docker run --rm -v ai-gateway_chat_edge_uploads:/uploads -v "<BACKUP_DIR>:/backups:ro" postgres:16 \
     sh -c 'cd /uploads && tar -xzf /backups/<stamp>/uploads.tar.gz'
   ```
   The volume's real name is `ai-gateway_chat_edge_uploads` (compose project
   + volume); `docker volume ls | grep uploads` confirms it.

6. **Start the stack and prove it.**
   ```sh
   docker compose -p ai-gateway up -d
   docker compose -p ai-gateway -f docker-compose.webchat.yml up -d
   docker compose -p ai-gateway -f docker-compose.webchat.yml run --rm backup now   # a fresh backup + drill of the restored state
   ```
   Then a real login and a real chat that opens an old conversation with a
   file. Drop `ai_gateway_broken_*` / `sran_ai_broken_*` a week later, not
   today.

## What the alert looks like

Channels (both optional, set in `.env`; nothing secret is ever put in a
message):

* **ntfy** — `ALERT_NTFY_URL=https://ntfy.sh/<your-topic>` (or a self-hosted
  ntfy). A failure is priority **high**, tag `rotating_light`; a success
  (only with `BACKUP_NOTIFY_SUCCESS=1`) is priority low, tag
  `white_check_mark`.
* **e-mail via Resend** — `RESEND_API_KEY` + `ALERT_EMAIL_TO=ops@example.com`
  (`ALERT_EMAIL_FROM` defaults to `no-reply@sran.ai`). Subject is
  `[high] SRAN backup FAILED` / `[ok] SRAN backup OK`.

The message is the one-line summary that also ends the container log:

```
Title:   SRAN backup FAILED                                    (priority high, tag rotating_light)
DRILL FAILED backup=/backups/20260923_031500 sizes: gateway.dump 41.2 MB, sran_ai.dump 3.8 MB, uploads.tar.gz 120.5 MB (57 entries); duration 38s; drill: FAIL 13/14 checks: rows:gw:request_logs

Title:   SRAN backup OK                                        (priority low, only with BACKUP_NOTIFY_SUCCESS=1)
OK backup=/backups/20260923_031500 sizes: gateway.dump 41.2 MB, sran_ai.dump 3.8 MB, uploads.tar.gz 120.5 MB (57 entries); duration 36s; drill: pass 14/14 checks
```

`BACKUP FAILED backup=none … drill: not run` means the dump itself failed
(server unreachable, wrong password, disk full); the container log has the
step that failed. If neither channel is configured the summary is only in the
log — configure one; a backup nobody hears about failing is the same as none.

## Proving it without the live stack

`scripts/probes/FIX129/backup_drill_check.sh` runs the image once against the
**test stack** (`scripts/test-stack.sh up`; the generator includes the
`backup` service and gives it its own `ts_backups` volume) and asserts:
manifest written, `drill.json` all pass, sizes reported. It refuses the live
network (`ai-gateway_default`) and any container that is not `sran-test-*`.
The 2026-09-23 run is under `scripts/probes/FIX129/results/ops/`.
