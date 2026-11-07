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
| Schedule | daily at `BACKUP_AT` = **03:15** in `TZ` = **Asia/Bangkok**, checked against the wall clock every minute and caught up after a missed window (FIX131, below) | `.env`: `BACKUP_AT=HH:MM`, `TZ=` |
| Freshness | a backup older than `BACKUP_MAX_AGE_H` = **26 h** is "not fresh": ops-watch pages (below) | `.env`: `BACKUP_MAX_AGE_H=` |
| Location | `BACKUP_DIR` on the host, default **`../ai-gateway-backups/daily`** (a sibling of the checkout, so a re-clone, `git clean` or worktree removal never eats it) → `/backups` in the container | `.env`: `BACKUP_DIR=/mnt/backups/sran` (put it on another disk on a real deployment) |
| Retention | directories older than `BACKUP_KEEP_DAYS` = **14** days are deleted at the end of each run | `.env`: `BACKUP_KEEP_DAYS=` |
| Success note | silent by default; `BACKUP_NOTIFY_SUCCESS=1` sends a quiet daily summary | `.env` |

The service runs with `restart: unless-stopped`, so it comes back with the
stack. One backup directory per run: `<BACKUP_DIR>/<YYYYMMDD_HHMMSS>/`. A run
that fails leaves `<YYYYMMDD_HHMMSS>.failed/` (whatever was written) so the
partial output can be inspected; it is pruned like the others.

## Schedule and catch-up (FIX131)

Until FIX131 the loop computed "next 03:15" once and slept that many seconds.
Docker Desktop's VM clock does not advance while the PC sleeps, so the run
due at 03:15 on 2026-09-24 fired at 05:52 (2 h 37 min late), and a window
missed while the container was down was simply skipped. Now
(`infra/backup/schedule.sh`):

* **Every `BACKUP_TICK_S` (60 s)** the loop reads the wall clock.
  `slot` = the latest `BACKUP_AT` in `TZ` that has passed. The backup is
  **due** when the newest *successful* backup (restore drill passed) finished
  before that slot. So after a missed window — PC asleep, container stopped,
  a failure — it runs at the first tick after it is back: **once**, not once
  per missed day.
* **Always `--no-deps` when a backup is run by hand.** After a compose file of the running stack has
  been edited, `docker compose run` first recreates every dependency whose settings changed — for the
  backup, the chat database it is about to dump (7 Oct 2026: it did, once; three seconds without a chat
  database). Every command in this document carries `--no-deps`; a backup with no database to reach
  fails loudly, which is the right answer.
* **No double runs.** A lock directory `<BACKUP_DIR>/.backup.lock` (holder,
  pid, time) is shared by the scheduled loop and `run --rm --no-deps backup now`
  (`now` refuses with exit 4 while a backup runs). A lock older than
  `BACKUP_LOCK_STALE_S` (3 h) is a crashed run's and is taken over (logged);
  a restarted container releases its own old lock at start. `mkdir` is used
  because it is atomic on every filesystem, the Windows bind mount included
  (`flock` is not reliable there).
* **Retry with backoff.** A failed run is retried after
  `BACKUP_RETRY_BASE_S` (10 min), then 20, 40, 80 min, capped at
  `BACKUP_RETRY_MAX_S` (2 h), until one succeeds; the next slot starts
  fresh. An off-site failure (exit 3) does not retry the local backup, which
  is proven.
* **One log line per decision** (on every change, and hourly while nothing
  changes):
  ```
  decision: wait — newest successful backup 2026-09-24T03:16:40+0700 is not older than the slot 2026-09-24T03:15:00+0700; next slot 2026-09-25T03:15:00+0700
  decision: run (catch-up, attempt 1) — slot 2026-09-24T03:15:00+0700 passed 2 h 37 min ago and was missed (PC asleep or container down); newest successful backup 2026-09-23T03:16:40+0700 (26 h 35 min old)
  decision: retry-wait — attempt 1 for the slot 2026-09-24T03:15:00+0700 failed at 2026-09-24T03:15:30+0700; retry 2 at 2026-09-24T03:25:30+0700 (backoff 10 min)
  decision: skip — another backup is running (a1b2c3d4 pid 17, since 45 s)
  ```
  `docker compose -p ai-gateway -f docker-compose.webchat.yml run --rm --no-deps backup tick`
  runs one decision by hand (and the backup, if due).

### The records

Every run writes one of two files in `BACKUP_DIR`, atomically (temp file +
rename in the same directory):

```json
last_success.json   {"at": "2026-09-23T20:16:40Z", "epoch": 1790194600, "slot": "2026-09-24T03:15:00+0700",
                     "trigger": "schedule|catch-up|retry|manual", "attempt": 1, "backup_id": "20260924_031500",
                     "duration_s": 100, "drill": {"pass": true, "checks_total": 14, "checks_failed": 0},
                     "sizes": {"gateway.dump": 205000000, "sran_ai.dump": 6800000, "uploads.tar.gz": 67500000, "total": 279300000},
                     "offsite": {"status": "pass|fail|not_configured", "snapshot": "1a2b3c4d…", "notified": false}}
last_failure.json   {"at": "…", "epoch": …, "slot": "…", "rc": 1, "status": "BACKUP FAILED|DRILL FAILED",
                     "backup_id": null, "attempts": 2, "next_retry_at": "…", "notified": false, "summary": "…"}
```

`last_success.json` is written for a run whose restore drill passed (exit 0,
or 3 = local backup fine, off-site copy failed — `offsite.status: "fail"`).
Before the first FIX131 run there is no record; the scheduler and ops-watch
then use the newest directory whose `drill.json` says `"pass": true`.

## The "no fresh backup" alert (FIX131)

Nothing used to alert on a *missing* backup: success was silent
(`BACKUP_NOTIFY_SUCCESS=0`) and a container that is stopped, or a schedule
that never fires, sends nothing. The backup container cannot report its own
death, so the check lives in **ops-watch**, which mounts the same folder
read-only (`${BACKUP_DIR}:/backups:ro`, `OPS_WATCH_BACKUP_DIR=/backups`) and
reads the records every 30 s — the dead man's switch pattern (the job
reports, a separate monitor alerts):

| Incident | When | Priority |
|---|---|---|
| `backup` — "Backups: no fresh backup for 30 h" | no proven backup newer than `BACKUP_MAX_AGE_H` (26 h: a day plus a 2 h grace) | high (4) |
| `backup` — "Backups are failing: the last backup did not complete / could not be restored in the drill" | `last_failure.json` newer than `last_success.json` | high (4) |
| `backup:offsite` — "Off-site backup copy failed (the local backup is fine)" | the newest success's `offsite.status` is `fail` | high (4) |

It pages through the shared pager (`docs/ops/monitoring.md`, "The pager"):
one page per incident, a reminder after 24 h if still open, and one
"Recovered: …" (low priority) when a fresh proven backup appears.

Who pages what, so one incident is one page: the backup container pages the
**transition** into failure itself (the first failed attempt since the
newest success, while that success is still fresh) and records
`"notified": true`; ops-watch then registers the incident as already paged
and owns everything after it — failed retries, staleness, the reminder and
"recovered". If the container did not page (a retry, the newest success
already stale, or its channel failed), ops-watch does. An off-site failure
is paged by the container on the first night only.

To see the check without paging anyone: `docker compose -p ai-gateway exec
ops-watch python -c "from app.ops.watch import backup_records, classify_backup; import time; s,f,n = backup_records('/backups'); print([x.title for x in classify_backup(s, f, now=time.time(), max_age_h=26, newest_proven=n)])"`
(prints `[]` when the backups are fresh).

## What is in a backup

```
20260923_031500/
  gateway.dump      pg_dump -Fc of the gateway DB    postgres / ai_gateway   (tenants, users, keys, request_logs, credits, models …)
  sran_ai.dump      pg_dump -Fc of the chat DB       postgres-webchat / sran_ai   (chat.* — conversations, messages, files, memories …)
  uploads.tar.gz    the chat_edge_uploads volume     uploaded + generated files (/app/data/uploads in chat-edge)
  manifest.json     sizes + sha256 of the three files, row counts of the key tables, versions, timing,
                    offsite.configured (FIX130)
  drill.json        the restore drill's verdict (written by the drill that follows every backup)
  offsite.json      the off-site copy's verdict (FIX130, below): snapshot, bytes uploaded, the
                    off-site restore drill's sha256 checks, retention, the weekly check
```

`uploads.tar.gz` is written with `gzip --rsyncable` (FIX130; same format,
readable by any `tar -xzf`): the compressor restarts at content-defined
points, so a file added to the volume changes only the bytes around it and
the off-site copy uploads roughly that file, not the whole archive again.

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
`run --rm --no-deps backup now` exit 2.

## Off-site copy (FIX130)

### Why: 3-2-1

The local backup sits on the same PC, the same disk, as the databases it
protects. It saves us from a bad transaction, a dropped table or a broken
migration — not from a dead disk, a stolen or burnt PC, or ransomware, which
take the backup together with the data.

| Copy | Where | Survives |
|---|---|---|
| 1. live data | Docker volumes on this PC | — |
| 2. local backup | `BACKUP_DIR` (`../ai-gateway-backups/daily`), same PC and disk | bad writes, dropped tables, a broken upgrade (14 days) |
| 3. **off-site** | a restic repository in an S3-compatible bucket at another company (Backblaze B2, Cloudflare R2, AWS S3 or a company S3) | the PC: disk, theft, fire, ransomware on this machine (see hardening) |

3-2-1 = three copies, on two kinds of storage, one of them off-site. Copy 2
and copy 1 share a disk, so today it is really "3-1-1"; putting `BACKUP_DIR`
on a second disk (USB / NAS) is what would make copy 2 a second medium.

### What runs

After every backup whose drill passed (`offsite.sh`, restic 0.19.1 pinned in
the image):

1. **upload** — `restic backup` of the run directory. restic encrypts on this
   machine before anything leaves (AES-256, key derived from the repository
   password — the bucket only ever holds ciphertext), splits files into
   content-defined chunks and uploads only chunks the repository does not
   have yet; every run is a snapshot (`--host sran --tag sran-backup`).
   First use creates the repository (`restic init`).
2. **off-site restore drill** — that snapshot is restored into a temp
   directory and the sha256 of `gateway.dump`, `sran_ai.dump`,
   `uploads.tar.gz` (and `manifest.json` itself) is compared with the local
   manifest. The copy is proven the same way the local one is.
3. **retention** — only once 2 passed: `restic forget --prune --group-by host`
   keeping **14 daily, 8 weekly, 12 monthly** snapshots (about a year of
   history; the local copy keeps 14 days).
4. **weekly check** — on `BACKUP_OFFSITE_CHECK_WEEKDAY` (7 = Sunday):
   `restic check --read-data-subset=5%` reads back a random 5 % of the stored
   data every week, which catches damage in the bucket that the daily drill
   (today's snapshot only) would not.

The verdict is `<run>/offsite.json` (snapshot id, bytes new vs processed,
each check) and the last part of the summary line. A failure in any step
exits 3 and sends the alert; success is quiet unless
`BACKUP_NOTIFY_SUCCESS=1`. With no `BACKUP_OFFSITE_REPOSITORY` the run logs
exactly one line, `offsite copy: not configured`, writes
`offsite.json {"configured": false}` and `manifest.offsite.configured=false`,
and the local backup passes as before.

Nothing secret is printed: every line restic writes passes through
`lib.sh redact` (the password, the keys, the literal repository and its
endpoint URL become `[redacted]` / `<offsite repository>`); the repository
is only ever named by scheme and host (`s3 s3.us-west-004.backblazeb2.com`).

Sizes and cost, from the live backup of 2026-09-23 (`gateway.dump` 205 MB,
`sran_ai.dump` 6.8 MB, `uploads.tar.gz` 67.5 MB, 649 files): the first
upload is ~280 MB; after that only changed chunks go up (the FIX130 proof on
synthetic data: 27 % of the second run was new). The daily off-site drill
downloads that day's snapshot (~280 MB): free egress on R2 and on B2 (up to
3× the stored amount a month), about $0.75/month on AWS S3. Storage for a
year of snapshots is a few GB — cents a month on any of them.

### Configuration

All in `.env` (never committed), passed to the `backup` service by
`docker-compose.webchat.yml`. The test stack never gets these: its generator
maps them to empty `TS_BACKUP_OFFSITE_*` twins.

| Variable | Meaning |
|---|---|
| `BACKUP_OFFSITE_REPOSITORY` | the restic repository, `s3:https://<endpoint>/<bucket>/sran` (see the provider steps). Empty = off. |
| `BACKUP_OFFSITE_PASSWORD` | the repository password = the encryption key. **Also kept off this machine** (below). |
| `BACKUP_OFFSITE_ACCESS_KEY_ID` / `BACKUP_OFFSITE_SECRET_ACCESS_KEY` | the bucket key (→ `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` for restic) |
| `BACKUP_OFFSITE_REGION` | optional (→ `AWS_DEFAULT_REGION`); R2 wants `auto` |
| `BACKUP_OFFSITE_KEEP_DAILY` / `_WEEKLY` / `_MONTHLY` | retention, default 14 / 8 / 12 |
| `BACKUP_OFFSITE_CHECK_WEEKDAY` | 1–7 (`date +%u`), default 7; `always` / `never` |
| `BACKUP_OFFSITE_CHECK_SUBSET` | default `5%` |
| `BACKUP_OFFSITE_TIMEOUT_S`, `BACKUP_OFFSITE_CONNECT_TIMEOUT_S` | ceilings per restic call (7200 s) and for the first contact (300 s) |

### The owner's one-time setup

1. **Create a private bucket and a key that can reach only that bucket.**
   * *Backblaze B2*: Buckets → Create a Bucket (`sran-backups`, Private).
     Application Keys → Add a New Application Key: *Allow access to
     Bucket(s)* = `sran-backups` only, *Type of Access* = Read and Write.
     Copy the keyID and applicationKey (shown once). The bucket page shows
     the S3 endpoint: `BACKUP_OFFSITE_REPOSITORY=s3:https://s3.<region>.backblazeb2.com/sran-backups/sran`.
   * *Cloudflare R2*: R2 → Create bucket (`sran-backups`). Manage R2 API
     Tokens → Create API token: *Object Read & Write*, *Apply to specific
     buckets only* = `sran-backups`. Copy the Access Key ID and Secret
     Access Key. `BACKUP_OFFSITE_REPOSITORY=s3:https://<account-id>.r2.cloudflarestorage.com/sran-backups/sran`,
     `BACKUP_OFFSITE_REGION=auto`.
   * *AWS S3 / a company S3*: a bucket with Block Public Access on; an IAM
     user (or the S3 admin's equivalent) with only this policy — the one the
     FIX130 proof enforced on MinIO:
     ```json
     {"Version": "2012-10-17", "Statement": [
       {"Effect": "Allow", "Action": ["s3:ListBucket", "s3:GetBucketLocation"], "Resource": ["arn:aws:s3:::sran-backups"]},
       {"Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], "Resource": ["arn:aws:s3:::sran-backups/*"]}]}
     ```
     `BACKUP_OFFSITE_REPOSITORY=s3:https://s3.<region>.amazonaws.com/sran-backups/sran`,
     `BACKUP_OFFSITE_REGION=<region>` (a company S3: its endpoint URL).
2. **Make the repository password, and store it off this machine first.**
   A long random one (a password manager's generator, or
   `openssl rand -base64 32`). Put it — with the repository string and the
   provider login — in the company password manager / vault (or printed, in
   a safe), **before** the first backup. The bucket key can be re-created
   from the provider's console; the password cannot. If this PC dies and the
   password lived only in its `.env`, the off-site copy is unreadable
   ciphertext and nobody — not the provider, not restic — can open it.
3. **Configure and prove.** Add the variables to `.env`, then:
   ```sh
   docker compose -p ai-gateway -f docker-compose.webchat.yml up -d --build backup   # new image + env
   docker compose -p ai-gateway -f docker-compose.webchat.yml run --rm --no-deps backup offsite-check
   #   … "repository does not exist yet … restic init", "round trip PASS", "test snapshot removed",
   #   "restic check PASS", "offsite: check PASS"   (exit 0)
   docker compose -p ai-gateway -f docker-compose.webchat.yml run --rm --no-deps backup now
   #   … summary: OK … drill: pass 14/14 checks; offsite: pass snapshot 1a2b3c4d, 270 MB new of 280 MB, restore 4/4 sha256
   ```
   `offsite-check` failing says why: `wrong repository password`, `the
   bucket did not answer in time (unreachable?)`, an upload/restore
   permission, or `could not remove the test snapshot` (a key without delete
   permission: retention will fail too — see hardening).
4. **Hardening (optional, recommended).** The key on this machine can delete
   (retention needs it), so malware on this PC could delete the bucket's
   content too. Turn on bucket versioning (B2: "Keep all versions" + a
   lifecycle rule; S3: Versioning + a lifecycle rule expiring noncurrent
   versions after 30 days; R2: bucket lock rules) so a deletion is
   recoverable for 30 days from the provider console, which the key on this
   PC cannot change.

### Restore from the off-site copy (the PC is gone)

You need three things from the password manager: the repository string, the
repository password, and a bucket key (the old one, or a new one from the
provider's console). On any machine with Docker and a clone of this repo:

```sh
docker build -t sran-backup infra/backup            # restic + pg_restore + jq, the versions we back up with
cat > restore.env <<'EOF'                           # not on the command line (shell history)
RESTIC_REPOSITORY=s3:https://…/sran-backups/sran
RESTIC_PASSWORD=…
AWS_ACCESS_KEY_ID=…
AWS_SECRET_ACCESS_KEY=…
AWS_DEFAULT_REGION=…                                # R2: auto; B2/S3: the region
EOF
mkdir -p restore
docker run --rm -it --env-file restore.env -v "$PWD/restore:/restore" --entrypoint bash sran-backup
restic snapshots --host sran --tag sran-backup                         # one per night; pick a stamp (tag id=<stamp>)
restic restore latest --host sran --tag sran-backup --target /restore  # or a snapshot id instead of latest
cd /restore/backups/<stamp>
jq -r '.files | to_entries[] | "\(.value.sha256)  \(.key)"' manifest.json | sha256sum -c -   # all OK
jq .pass drill.json                                                     # the night's own drill verdict
```

Then "Restore for real" below, steps 3–6, with `/restore/backups` as
`BACKUP_DIR` (mount it at `/backups`). On a new machine bring up only the two
database containers first (`docker compose -p ai-gateway up -d postgres`,
`… -f docker-compose.webchat.yml up -d postgres-webchat`, with a `.env`
rebuilt from the password manager), restore into them, then start the rest.
`shred`/delete `restore.env` afterwards.

The FIX130 proof did exactly this from the bucket alone, in a container with
no access to the local backups: 3/3 sha256 matched the manifest, and
`pg_restore --list` read both dumps.

### Proving it without a real bucket

`scripts/ops/offsite_e2e.sh [image] [results dir]` builds the whole
path on throwaway containers — MinIO with a bucket and a least-privilege
user, two seeded Postgres 16 servers, a dummy alert endpoint — and asserts:
first-time `offsite-check`, `now` twice (upload, off-site drill, retention,
check; dedup on the second run), a restore on a "new machine" from the
bucket alone, a wrong password and an unreachable bucket (exit 3 + an alert
to the dummy endpoint, local backup still proven), not configured (one line,
exit 0, no alert), and no secret in any log. The 2026-09-23 run: all 20
assertions PASS; the same script against the FIX129 image: 15 FAIL.

### A synced folder instead of a bucket (FIX133 C)

The repository does not have to be a bucket. Any folder this PC syncs
somewhere else — OneDrive, Google Drive, a NAS share — works the same way:
restic encrypts and deduplicates locally and writes only ciphertext into the
folder, the sync client carries it off the machine. In `.env`:

```
BACKUP_OFFSITE_DIR=C:/Users/<you>/OneDrive/sran-backups     # the synced folder (host path)
BACKUP_OFFSITE_REPOSITORY=/offsite                           # where the container sees it
BACKUP_OFFSITE_PASSWORD=<long random passphrase — keep a copy OFF this PC>
```

then `docker compose -p ai-gateway -f docker-compose.webchat.yml up -d backup`
and `run --rm --no-deps backup offsite-check`. No keys, no account. The trade-off: the
sync client must be signed in and running for the copy to leave the PC, and
a ransomware that reaches the synced folder reaches the copy (restic's
encryption keeps it unreadable, not undeletable — keep the bucket option for
that). The default `BACKUP_OFFSITE_DIR` (`../ai-gateway-backups/offsite`) is
only a mount point: on this PC it is NOT off-site until it points at a synced
folder.

### Choosing the destination (FIX134 C5)

Today `BACKUP_OFFSITE_DIR` is `../ai-gateway-backups/offsite`: a restic
repository on the same disk as the live data. The copy, the drill and the
restore all work (proven nightly), but the copy is not off-site until the
owner picks one of these. Each one is a `.env` change plus one `up -d backup`;
`offsite-check` proves it and `restore-offsite` restores from it.

| option | what it needs from the owner | `.env` | notes |
|---|---|---|---|
| **1. Synced folder** (OneDrive / Google Drive / NAS share) | sign in to the sync client on this PC (the OneDrive folder exists but is empty: not signed in) and create the folder | `BACKUP_OFFSITE_DIR=C:/Users/<you>/OneDrive/sran-backups`, `BACKUP_OFFSITE_REPOSITORY=/offsite` | nothing else changes; the sync client must stay signed in; ransomware that reaches the folder reaches the copy (encrypted, not undeletable) |
| **2. External drive** | plug in a drive that stays plugged in (none was present on 2026-09-25) | `BACKUP_OFFSITE_DIR=E:/sran-backups`, `BACKUP_OFFSITE_REPOSITORY=/offsite` | the copy fails (and pages after 48 h) while the drive is out; take it off-site by hand |
| **3. Bucket** (S3-compatible: Backblaze B2, Cloudflare R2, AWS) | create a bucket and an access key with write + list + read | `BACKUP_OFFSITE_REPOSITORY=s3:https://<endpoint>/<bucket>/sran`, `BACKUP_OFFSITE_ACCESS_KEY_ID`, `BACKUP_OFFSITE_SECRET_ACCESS_KEY`, `BACKUP_OFFSITE_REGION` | the only option that survives this PC and the sync account being lost together; ~US$1/month for 300 MB |

In every case `BACKUP_OFFSITE_PASSWORD` stays what it is (rotated in FIX133)
and a copy of it lives off this PC. Switch:

```
# edit .env as in the table, then
docker compose -p ai-gateway -f docker-compose.webchat.yml up -d backup
docker compose -p ai-gateway -f docker-compose.webchat.yml run --rm --no-deps backup offsite-check
docker compose -p ai-gateway -f docker-compose.webchat.yml run --rm --no-deps backup copy-offsite     # first copy now
docker compose -p ai-gateway -f docker-compose.webchat.yml run --rm --no-deps backup restore-offsite  # prove it
```

The pager's `Off-site backup copy is stale` rule (48 h) covers all three.

### Restore from the off-site copy (FIX133 C)

```
docker compose -p ai-gateway -f docker-compose.webchat.yml run --rm --no-deps backup restore-offsite            # newest snapshot
docker compose -p ai-gateway -f docker-compose.webchat.yml run --rm --no-deps backup restore-offsite <snapshot> [dir]
```

restores the run directory from the OFF-SITE repository (never the local
backup) into `/backups/restore-offsite-<timestamp>/`, checks every file's
sha256 against the manifest and writes `restore-offsite.json` (`pass`,
`files`, `duration_s`). From there, "Restore for real" below loads the dumps.
The alert `Off-site backup copy is stale` (ops-watch, `OPS_WATCH_OFFSITE_MAX_AGE_H`,
48 h) fires when no proven off-site copy is younger than two days; `No off-site
backup copy is configured` is an opt-in reminder (`OPS_WATCH_OFFSITE_REMIND=1`).

## Run one now

```sh
docker compose -p ai-gateway -f docker-compose.webchat.yml run --rm --no-deps backup now
```

That is backup + drill + off-site copy + notification, then exit (0 =
written and proven, and the off-site copy proven or not configured; 1 = the
backup failed; 2 = the drill failed; 3 = the off-site copy failed — the
local backup is still there and still proven; 4 = another backup is running,
nothing started). It takes the same lock as the scheduled loop and writes
the same records, so a manual run counts for tonight's slot. Sub-commands,
when you need one piece:

```sh
docker compose -p ai-gateway -f docker-compose.webchat.yml run --rm --no-deps backup backup            # dump only
docker compose -p ai-gateway -f docker-compose.webchat.yml run --rm --no-deps backup drill /backups/20260923_031500
docker compose -p ai-gateway -f docker-compose.webchat.yml run --rm --no-deps backup offsite /backups/20260923_031500
docker compose -p ai-gateway -f docker-compose.webchat.yml run --rm --no-deps backup offsite-check     # first-time setup / "is it still working?"
docker compose -p ai-gateway -f docker-compose.webchat.yml run --rm --no-deps backup tick              # one scheduler decision (FIX131)
docker compose -p ai-gateway -f docker-compose.webchat.yml run --rm --no-deps backup notify info "test" "hello from the backup service"
```

`notify` is a **test fire** (FIX131): always `[TEST]` in title and body, and
sent only to `ALERT_TEST_NTFY_URL` (a throwaway topic) — never the owner's
topic, its token or e-mail. Without `ALERT_TEST_NTFY_URL` it is logged only.

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
   answer: `run --rm --no-deps backup backup`. It costs a minute and keeps the option
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
   docker compose -p ai-gateway -f docker-compose.webchat.yml run --rm --no-deps backup now   # a fresh backup + drill of the restored state
   ```
   Then a real login and a real chat that opens an old conversation with a
   file. Drop `ai_gateway_broken_*` / `sran_ai_broken_*` a week later, not
   today.

## What the alert looks like

Channels (both optional, set in `.env`; nothing secret is ever put in a
message):

* **ntfy** — `ALERT_NTFY_URL=https://ntfy.sh/<your-topic>` (or a self-hosted
  ntfy); `ALERT_NTFY_TOKEN` (FIX130) adds `Authorization: Bearer <token>`
  for a reserved / access-controlled topic. A failure is priority **high**,
  tag `rotating_light`; a success (only with `BACKUP_NOTIFY_SUCCESS=1`) is
  priority low, tag `white_check_mark`. Since FIX130 the title and message
  pass through `lib.sh redact` before they are sent (see
  `docs/ops/monitoring.md`, "What an alert may carry").
* **e-mail via Resend** — `RESEND_API_KEY` + `ALERT_EMAIL_TO=ops@example.com`
  (`ALERT_EMAIL_FROM` defaults to `no-reply@sran.ai`). Subject is
  `[high] <title>`.
* `ALERT_ENVIRONMENT` (FIX131): empty on the real system; `test` (the test
  stack sets it) puts `[TEST]` in front of title and message and sends only
  to `ALERT_TEST_NTFY_URL` — never `ALERT_NTFY_URL`, its token or e-mail.

Since FIX131 the title says what is wrong and the message what still works
and what to do; the one-line summary that also ends the container log
follows as `Details:`. The container sends these on the transition into
failure only (see "The no fresh backup alert" for who sends what after):

```
Title:   Backup failed: no new backup was written              (priority high, tag rotating_light)
Tonight's backup of the databases and uploads did not complete. The previous backup is still there:
2026-09-23 03:16 +07 (23 h 59 min old). The next automatic try is in 10 min. Look: docker logs
ai-gateway-backup (the step that failed). Run one by hand: docker compose -p ai-gateway -f
docker-compose.webchat.yml run --rm --no-deps backup now
Details: BACKUP FAILED backup=none sizes: n/a; duration 3s; drill: not run; offsite: not run

Title:   Backup failed its restore test                        (priority high)
… Details: DRILL FAILED backup=/backups/20260923_031500 sizes: gateway.dump 41.2 MB, …; drill: FAIL 13/14 checks: rows:gw:request_logs

Title:   Off-site backup copy failed (the local backup is fine)   (priority high; exit 3)
… Details: OFFSITE FAILED backup=/backups/20260923_171923 …; drill: pass 14/14 checks; offsite: FAIL at connect (wrong repository password)

Title:   SRAN backup OK                                        (priority low, only with BACKUP_NOTIFY_SUCCESS=1)
OK backup=/backups/20260923_031500 sizes: …; drill: pass 14/14 checks; offsite: pass snapshot 1a2b3c4d, 9.1 MB new of 165.5 MB, restore 4/4 sha256
```

(The off-site `Details` line is verbatim from the FIX130 proof's dummy
endpoint; the other failure reasons read `the bucket did not answer in time (unreachable?)`,
`FAIL at upload: upload`, `FAIL at restore: restore-sha256:gateway.dump`, …)

`BACKUP FAILED backup=none … drill: not run` means the dump itself failed
(server unreachable, wrong password, disk full); the container log has the
step that failed. If neither channel is configured the summary is only in the
log — configure one; a backup nobody hears about failing is the same as none.

## Proving it without the live stack

`scripts/ops/backup_drill_check.sh` runs the image once against the
**test stack** (`scripts/test-stack.sh up`; the generator includes the
`backup` service and gives it its own `ts_backups` volume) and asserts:
manifest written, `drill.json` all pass, sizes reported. It refuses the live
network (`ai-gateway_default`) and any container that is not `sran-test-*`.
It was last run on 2026-09-23 (all pass).
