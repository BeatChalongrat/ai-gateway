# Monitoring and self-healing (FIX129)

What the 2026-09-22 incident (NIGHT2) showed: the backend wedged for 34
minutes on one abandoned `SELECT … FOR UPDATE` transaction. Every Postgres
timeout was 0, so the lock was held until the session died; the backend's
`/ready` healthcheck failed the whole time, but `restart: unless-stopped`
only reacts to a process exiting — a container that is "Up (unhealthy)" is
left alone; `alert_rules` was empty, so nobody was told. Three fixes, each
with its own evidence.

## Postgres server-level safety nets (A1)

Both servers (`postgres` in `docker-compose.yml`, `postgres-webchat` in
`docker-compose.webchat.yml`) now start with:

| Setting | Value | Effect |
|---|---|---|
| `idle_in_transaction_session_timeout` | `120000` (120 s) | a session that sits inside an open transaction doing nothing for 120 s is terminated and its locks are released — the last resort for clients that bypass the per-service limits below (psql, scripts); the services themselves heal at 30 s, and the "transaction stuck > 60 s" alert fires before the server kill |
| `lock_timeout` | `30000` (30 s) | a statement that waits more than 30 s for a lock fails with `55P03 lock_not_available` instead of queueing forever |
| `statement_timeout` | **not set** (0) | on purpose: `pg_dump`, `VACUUM`, migrations and the backup drill need long statements; the services set their own per-connection `statement_timeout` in code |

Check on a running server (read-only):

```sh
docker exec ai-gateway-postgres-1       psql -U postgres -d ai_gateway -At -c "show idle_in_transaction_session_timeout" -c "show lock_timeout"
docker exec ai-gateway-postgres-webchat psql -U chatedge -d sran_ai    -At -c "show idle_in_transaction_session_timeout" -c "show lock_timeout"
```

Expected `2min` / `30s`. `0` / `0` means the container was not recreated
after the compose change (`docker compose … up -d postgres` recreates it;
`restart` does NOT re-read the command). `docker-compose.prod.yml` overrides
only `ports` and `restart` for `postgres`, so the command survives the prod
overlay.

A terminated idle transaction shows in the server log as
`FATAL: terminating connection due to idle-in-transaction timeout`; the
client sees its next statement fail and must reconnect (pool clients do).

### Per-service limits (set on every connection, in code)

The server net above is the last resort. Each service sets its own limits on
every connection it opens, and names itself in `pg_stat_activity`
(`application_name`), so the next incident report can say who held the lock:

| Service | `statement_timeout` | `lock_timeout` | `idle_in_transaction_session_timeout` | `application_name` | Where |
|---|---|---|---|---|---|
| backend (sync + async engine) | 60 s | 10 s | 30 s | `sran-backend`, `sran-backend-async` | `backend/app/core/db.py` (`DB_STATEMENT_TIMEOUT_MS`, `DB_LOCK_TIMEOUT_MS`, `DB_IDLE_IN_TRANSACTION_TIMEOUT_MS`) |
| backend — the credit deduction only | (inherits) | **5 s** | **15 s** | | `SET LOCAL` in `portal_credit_check.deduct_portal_credit_async` (`credit_deduction_lock_timeout_ms`, `credit_deduction_idle_timeout_ms`) |
| chat-edge (pgxpool) | 30 s | 5 s | 30 s | `chat-edge` | `services/chat-edge-api/internal/db/pool.go` (`PG_STATEMENT_TIMEOUT_MS`, `PG_LOCK_TIMEOUT_MS`, `PG_IDLE_IN_TRANSACTION_TIMEOUT_MS`) |
| ops-watch (its pg_stat_activity read) | 5 s | 2 s | — | `ops-watch` | `backend/app/ops/watch.py` |

A backend statement that legitimately needs more than 60 s (a long
migration, a big archive step) must `SET LOCAL statement_timeout = 0` for
itself; the schema guard's `alembic upgrade` runs on the same engine, so a
future long-running migration would hit the 60 s wall unless it does.

### The credit deduction is instrumented (A2)

The transaction that wedged the live system (`SELECT … FROM portal_users …
FOR UPDATE` in `deduct_portal_credit_async`) could not be reproduced, so it
is observed rather than "fixed":

* one log line per deduction — `credit_deduction request_id=… user_id=…
  outcome=ok|lock_timeout|cancelled|error credits=… elapsed_ms=…
  lock_wait_ms=… slow=…` — INFO normally, WARNING when slow
  (`credit_deduction_slow_seconds`, 2 s) or not ok;
* `/metrics`: `credit_deduction_inflight` (stays > 0 when one is stuck),
  `credit_deduction_seconds`, `credit_deduction_lock_wait_seconds`,
  `credit_deduction_total{outcome}`;
* the pg_stat_activity sampler (below) names the pid / application_name /
  query of anything idle in transaction or blocked on a lock.

## The pg_stat_activity sampler and `/ready` (A2/A3)

`backend/app/services/pg_activity.py` reads `pg_stat_activity` every
`pg_activity_sample_seconds` (15 s) for sessions idle in transaction or
waiting on a lock, publishes `pg_idle_in_transaction_sessions`,
`pg_idle_in_transaction_max_age_seconds`, `pg_lock_waiters`,
`pg_stuck_transaction_age_seconds`, and logs `pg_stuck_transaction` with the
offending sessions once the oldest passes `pg_stuck_transaction_seconds`
(60 s). `/ready` reads the last sample: `checks.pg_activity`, and
`failures: ["stuck_transaction"]` (503) at the same threshold — so the
container healthcheck goes red and autoheal restarts the backend, which
closes its connections and releases the lock. `/ready` also reports
`checks.turns.last_success_age_s` / `last_attempt_age_s` (every worker of
the container since FIX131 — see docs/ops/deploy-backend.md).

## Alerts (A4)

### Channels

Configured in `.env` (never committed), read by the backend, the ops
watcher and the backup job:

| Variable | Channel | Notes |
|---|---|---|
| `ALERT_NTFY_URL` | ntfy push (phone) | a full topic URL, e.g. `https://ntfy.sh/<random topic>`; the owner subscribes to it in the ntfy app. `ALERT_NTFY_TOKEN` for a protected topic. |
| `ALERT_EMAIL_TO` | e-mail via Resend | needs `RESEND_API_KEY`; in `APP_ENV=development` the sender only logs |
| `ALERT_WEBHOOK_URL` | JSON POST | the same payload the alert rules' own `webhook_url` gets |
| `ALERT_ENVIRONMENT` (FIX131) | label + route | empty / `production` / `live` = the real system. Anything else prefixes every title and body (`[STAGING] …`); `test` / `testing` / `ci` / `lane` / `test-stack` prefixes `[TEST]` AND sends every alert only to `ALERT_TEST_NTFY_URL` — never to the three channels above |
| `ALERT_TEST_NTFY_URL` (FIX131) | throwaway ntfy topic | where test alerts go (a test environment, `python -m app.ops.fire_test_alert`, `backup notify …`); empty = test alerts are logged only. Never gets `ALERT_NTFY_TOKEN`. |

A notification carries a title, a body, a priority (critical = 5, warning =
4, info = 3, low = 2) and tags; it never carries a secret. Since FIX131 the
title says what is wrong for users and the body what still works, what to do
and where to look; the numbers (window, metric value, threshold) stay in the
log line and `alert_events`. Example (ntfy):

```
Title:    Chat is failing: no reply succeeded in 5 min (12 tries)
Priority: 5
Tags:     rotating_light,critical,incident
Body:     12 turn(s) attempted in the last 5 min, none completed (12 failed). Users get errors instead
          of answers. Look: admin → Requests (status, error_code), docker logs
          ai-gateway-chat-edge-api --tail 100, and the model provider keys.
```

### What an alert may carry (FIX130)

What happened and the numbers — never SQL, credentials, tokens or user text.
Before FIX130 the stuck-transaction alert carried the head of the oldest
session's SQL in the ntfy / e-mail body, the whole `sessions` list with each
query in `alert_events.details_json`, the `alert_emitted` log line and the
rule's webhook payload, and the ops watcher put `query[:100]` in its
message. A query holds whatever the application bound into it — message
text, e-mail addresses, tokens. Now:

```
Title:    Transaction stuck longer than 60 s: a database transaction has been idle for 64 s (limit 60 s) — app psql, pid 262
Body:     … stuck_age_s: 64.2
          pid 262 idle in transaction psql 64.2 s
ops-watch: transaction stuck 64 s on chat — pid 262 idle in transaction (psql, wait Client) for 64 s (limit 60 s) — 1 session(s) idle/blocked
```

* The query head is kept in ONE place: the `pg_stuck_transaction` log line
  (backend: `pg_activity.record_sample`; ops-watch: its own log). That is
  where the incident report reads it from.
* `alert_scrub` (backend) and `lib.sh redact` (backup image) are the last hop
  before anything leaves or is stored: `AlertService` (log line,
  `alert_events` row, rule webhook) and `alert_channels.deliver` (ntfy,
  e-mail, webhook — so the ops watcher too) drop `query` / `sql` /
  `statement` / `content` / `prompt` / `messages` / `body` keys at any
  depth and replace SQL statements, credentials in URLs, JWTs, `key=value`
  secrets, API-key-shaped and long random tokens; `notify.sh` does the same
  plus the literal values of the backup service's secrets.
* The channel URLs themselves are credentials (next section): httpx logs
  every request URL at INFO, so a filter on the `httpx` logger shows the
  configured ntfy / webhook URLs and every rule webhook as
  `scheme://host/…`; `WebhookService` logs the same label.
* Proven by `backend/tests/test_fix130_alert_redaction.py` (every builder fed
  a session whose query holds SQL, a DSN with a password, a JWT, an API key
  and Thai user text), `test_fix130_alert_url_not_logged.py` and
  `infra/backup/tests/test_notify.sh`.
* The unit lane cannot page anyone: `conftest._alert_channels_off_by_default`
  blanks the global channels (FIX131: and `ALERT_TEST_NTFY_URL`,
  `ALERT_ENVIRONMENT`, and gives each test its own in-memory pager store —
  never Redis) for every test (the lane's environment is the
  live backend's — on 2026-09-23 it sent 17 fake "test-rule: …" alerts to the
  owner's topic), and the test stack gets `TS_ALERT_*` twins, empty unless
  set (`scripts/test_stack_gen.py` refuses a file that references a live
  alert variable).

### The ntfy topic is the secret (FIX130)

On ntfy.sh, a topic with no reservation has no password: whoever knows the
topic name can subscribe and read every alert (and publish fake ones). The
alerts now carry no data, but they still describe the system (which service
is down, when, how often). Measured on 2026-09-23 without printing either
topic:

| Topic | Length | Random part | Strength |
|---|---|---|---|
| owner (`ALERT_NTFY_URL` in `.env`) | 36 chars | 24 hex characters after a readable prefix | ~96 bits — cannot be guessed |
| FIX129 test topic (the running test stack) | 33 chars | 16 hex characters | ~64 bits — cannot be guessed either, but weaker; it is a test topic, keep it away from real alerts |

Guessing is not the risk; leaking is. The owner topic reached places it
should not: 17 fake alerts from the unit lane (07:20–07:23Z and
09:22–09:23Z), and one lane's tool output printed the full topic URL (pytest
echoed httpx's `HTTP Request: POST https://ntfy.sh/<topic>` line). The
extended log secret scan (below) found the topic in no container log, live
or test. **Rotate the owner topic after this card** (new random topic in
`.env` → recreate `backend`, `ops-watch`, `backup`; resubscribe the phone),
whichever option below is chosen.

Three ways to run the channel:

| | Keep a random public topic (today) | Reserved topic on ntfy.sh (paid tier) | Self-hosted ntfy |
|---|---|---|---|
| Security | the topic name is the only secret; anyone who learns it (a log, a screenshot, a leaked `.env`) reads and can post until it is rotated | the topic is reserved to our account; read and publish need an access token (`ALERT_NTFY_TOKEN`, supported since FIX130 by the backend, ops-watch and the backup job); a leaked topic name alone is useless | same token/ACL model (`auth-default-access: deny-all`, users + tokens) and the data never touches a third party |
| Effort | none — rotate the topic after a leak | an ntfy.sh account and a small monthly fee, reserve the topic, create a token with publish rights; set `ALERT_NTFY_TOKEN`; the phone app logs in to read | run and patch another service (container, TLS certificate, a public hostname so the phone can reach it, backups of its user DB), and keep it up |
| If this PC dies | alerts keep working — ntfy.sh is elsewhere (but nothing is left to send them: ops-watch and the backup job die with the PC; see below) | same as today | **a self-hosted ntfy on this PC dies with it** — the one moment an alert matters most, the channel is gone; it only makes sense on a different machine / VPS, which is a new machine to run |

Recommendation: a **reserved ntfy.sh topic with an access token**, plus
e-mail (`RESEND_API_KEY` + `ALERT_EMAIL_TO`) as a second, independent
channel. It closes "the name is the secret" for a small fee and no new
server; self-hosting only makes sense once there is a second machine to run
it on. Whatever is chosen, every alert source runs on this PC — if the PC
dies, nothing is sent at all. The missing piece is an outside heartbeat (an
external uptime monitor that polls the public health URL and e-mails when it
stops answering) that alerts when the PC goes silent; that is a follow-up,
not part of FIX130.

### The pager: one incident, one page (FIX131)

NIGHT3 (2026-09-23): one stuck transaction produced FOUR pushes (the rule
"Transaction stuck longer than 60 s" p4, ops-watch "backend is not ready:
/ready answered 503: stuck_transaction" p5, "transaction stuck 80 s on
gateway" p5, "… on chat" p5); a planned backend reload paged p5; one failed
turn in a quiet 5 min paged twice (the rule p4 and its ops-watch twin p5); a
test-stack page looked like a real one. Each sender had its own cooldown and
graded severity by its own rule.

Now the backend's alert rules and ops-watch hand every finding to one pager
(`backend/app/services/alert_pager.py`; groups, severities and words in
`alert_catalog.py`), with its state in the backend's Redis (db 0, keys
`sran:alert:incident:<group>`, a short lock per group), so both processes —
and every backend worker — decide together. If Redis does not answer, each
process decides from its own memory (logged `pager_store_fallback`; worst
case one page per sender), and ops-watch keeps that memory in its state file.
This follows the PagerDuty / Alertmanager model: a dedup key per incident,
grouping, a repeat interval, inhibition during maintenance.

**Grouping** — related signals are one incident:

| Incident (group) | Signals |
|---|---|
| `service:<service>` | container unhealthy / restarting / exited; for `backend` also "/ready does not answer" and "/ready 503" for any reason but a stuck transaction |
| `db:gateway:stuck` | backend rule `stuck_transaction` + ops-watch `pg:gateway:stuck` + "/ready 503" whose only reason is `stuck_transaction` |
| `db:chat:stuck` | ops-watch `pg:chat:stuck` (another database = another incident) |
| `turns:none_succeeded` / `turns:error_rate` | the backend rules `no_successful_turn` / `error_rate` + their ops-watch twins |
| `turns:stuck` | ops-watch: assistant replies still `streaming` > 15 min (last 24 h) |
| `backup` / `backup:offsite` | ops-watch from the backup records (`docs/ops/backups.md`) |
| `disk:<path>`, `memory` | ops-watch |
| `power` | ops-watch from the host's power reading: on battery, battery low, or the reading has stopped (below) |
| `rule:<id>` | any other (operator-defined) rule |

**Severity by impact:**

| Priority | When |
|---|---|
| critical (ntfy 5) | users cannot use the system: a user-facing service (`backend`, `gateway`, `chat-edge-api`, `web-chat`, both Postgres, both Redis) down or not ready past the debounce; no successful turn with at least `ALERT_PAGE_MIN_ATTEMPTS` (3) attempts |
| warning / high (ntfy 4) | degraded: error rate, a stuck transaction, replies stuck, disk / memory high, backups, another service down, any operator rule |
| low (ntfy 2, silent) | "Recovered: …" / "No longer reported: …" |
| none (log only) | one or two failed turns in a quiet window; a restart that heals within the debounce |

**When it pages:**

* once per incident, when its severity is above none and it has lasted
  `ALERT_DEBOUNCE_SECONDS` (90 s) for container / readiness findings (a
  normal restart heals first; everything else pages at once);
* again only on **escalation** (a higher severity than the one paged — "one
  failed turn" becoming "three") or as a **reminder** while still open:
  after `ALERT_REMINDER_SECONDS` (4 h, Grafana's default repeat interval)
  for critical, `ALERT_REMINDER_HIGH_SECONDS` (24 h) for high;
* one **"Recovered: …"** when every sender has cleared it; "No longer
  reported: …" when its senders stopped reporting (the check itself stopped
  running) — never a "recovered" for something that was never paged;
* a failed delivery (every channel refused) is not counted as paged and is
  tried again on the next sighting.

(`ALERT_DEBOUNCE_SECONDS`, `ALERT_PAGE_MIN_ATTEMPTS`, `ALERT_REMINDER_SECONDS`
and `ALERT_REMINDER_HIGH_SECONDS` are backend settings with these defaults;
compose does not pass them — to change one, add it to both the `backend`
and `ops-watch` environment in `docker-compose.yml`.)

Every decision is logged: `pager_decision action=page|escalate|remind|grouped|hold|maintenance|log_only|recovered|expired|closed_unpaged group=… source=… reason=…`
(a repeat of the same non-paging decision — grouped, hold, maintenance, log_only — is not). An `alert_events` row is
still written for every fired rule; `delivery_status` says `grouped`,
`hold`, `maintenance` or `log_only` when the pager did not send it.

**Maintenance window** — planned work never pages:

```sh
docker compose -p ai-gateway exec -T ops-watch python -m app.ops.maintenance start 15 "deploy FIX131"
docker compose -p ai-gateway exec -T ops-watch python -m app.ops.maintenance status
docker compose -p ai-gateway exec -T ops-watch python -m app.ops.maintenance stop
```

A Redis key `sran:ops:maintenance` with a TTL (so a forgotten window ends by
itself; capped at 480 min). While it is open every finding is logged
(`action=maintenance`) and nothing is sent; an incident still open after the
window pages normally and says it began during maintenance. Exit 1 = Redis
did not answer and the window is NOT set. A deploy script: `start` before the
first restart, `stop` after the last healthcheck is green.

**Test alerts never reach the owner** (`ALERT_ENVIRONMENT`,
`ALERT_TEST_NTFY_URL` above): the test stack's generator sets
`ALERT_ENVIRONMENT=test` on its backend and backup services
(`scripts/test_stack_gen.py`), so every test-stack alert is `[TEST]` and goes
to `TS_ALERT_TEST_NTFY_URL` or nowhere. A test fire on the live system:

```sh
docker compose -p ai-gateway exec -T ops-watch python -m app.ops.fire_test_alert "checking the pager"
docker compose -p ai-gateway -f docker-compose.webchat.yml run --rm --no-deps backup notify info "test" "hello"
```

Both are always `[TEST]` and go only to `ALERT_TEST_NTFY_URL` (nothing is
sent without it). There is no admin "send a test alert" endpoint; a rule
created in the admin UI to try the pipeline pages for real — use
`fire_test_alert`, or `ALERT_ENVIRONMENT=test` on a test stack.

### Rules (backend, `alert_rules`, evaluated every `alert_sweep_interval_seconds` = 10 s)

Seeded at startup when no rule with the metric exists (`alert_defaults.py`;
an operator's own rule with the same metric is never overridden):

| Rule | Metric | Fires when | Cooldown |
|---|---|---|---|
| No successful turn in 5 min (with attempts) | `no_successful_turn` | ≥ 1 request in the window and 0 with `status=success` — a quiet night is not an outage. Gateway-level: only requests that reached a provider are in `request_logs` (see the ops watcher's turn rules for the chat-level truth) | 5 min |
| Error rate above 5 % (5 min, ≥10 requests) | `error_rate` | ≥ 10 requests in the window, > 5 % have a status other than `success` / `blocked`, **and at least `ALERT_PAGE_MIN_ATTEMPTS` (3) of them failed** — one failed request in ten is not a page (FIX131; same gateway-level caveat; ops-watch's turn rate uses the same floor) | 5 min |
| Transaction stuck longer than 60 s | `stuck_transaction` | the sampler's oldest idle / lock-blocked transaction is ≥ 60 s | 1 min |

Every fired rule is written to `alert_events` (`delivery_status`
delivered / failed / log_only, and since FIX131 grouped / hold / maintenance)
and handed to the pager, which decides what is sent. Since FIX131 each rule
is computed on every sweep, also inside its cooldown, so the pager hears
when it clears ("recovered") or escalates.

### The ops watcher (`ops-watch`, `backend/app/ops/watch.py`)

The alerts the backend cannot raise about itself, every `OPS_WATCH_INTERVAL`
(30 s), handed to the pager above (FIX131: `OPS_WATCH_COOLDOWN`, the old
10-minute re-announce, is retired):

| Finding | Source | Threshold |
|---|---|---|
| `<service> is unhealthy` / `is restarting` / `has exited` | Docker API (read-only socket), containers of the compose project | any |
| `backend is not ready` / `does not answer /ready` | `GET http://backend:8080/ready` from outside | 503 or no answer in 6 s |
| `disk /host/docker is N % full` | `statvfs` of the Docker data root | `OPS_WATCH_DISK_PCT` (90) |
| `memory is N % used` | `/proc/meminfo` of the Docker VM | `OPS_WATCH_MEM_PCT` (90) |
| `transaction stuck N s on gateway|chat` | `pg_stat_activity` on both databases (`OPS_WATCH_PG_DSNS`) | `OPS_WATCH_PG_STUCK_S` (60) |
| `no successful turn in 5 min` / `N % of turns failed (5 min)` | `chat.messages_masked` assistant rows `completed` / `error` on the chat database (the `chat` DSN) | ≥ 1 turn and 0 completed (pages from 3 attempts); ≥ `OPS_WATCH_TURN_MIN_SAMPLE` (10) turns and more than `OPS_WATCH_TURN_ERROR_PCT` (5) % failed |
| FIX131: `N replies stuck 'typing' for over 15 min` | assistant rows still `streaming`, created 15 min – 24 h ago (`OPS_WATCH_TURN_STUCK_MIN`, `OPS_WATCH_TURN_STUCK_LOOKBACK_H`) — the orphaned-turn sweep should have closed them | ≥ 1 |
| FIX131: `no fresh backup` / `backups are failing` / `off-site copy failed` | the backup folder, read-only (`OPS_WATCH_BACKUP_DIR=/backups`): `last_success.json`, `last_failure.json` (`docs/ops/backups.md`) | newest proven backup older than `BACKUP_MAX_AGE_H` (26 h); a failure newer than the last success |
| `the host is on battery` / `battery at N % — plug the host in now` / `power reading has stopped` | `power_last.json` in the backup folder, written once a minute by the host's task "SRAN power watch" (`scripts/ops/power-watch.ps1`) | on battery for 2 min; charge ≤ `OPS_WATCH_POWER_PAGE_PCT` (40 %) or time left ≤ `OPS_WATCH_POWER_PAGE_MIN` (60 min); reading older than 10 min |

(The titles above are the checks; since FIX131 the page itself is worded for
users — "Chat is down: backend is unhealthy", "Server disk is 92 % full" —
see the pager section.)

#### The host's power

The host is a laptop, and the pager stops with the machine: when the battery
is empty nothing is left to say so. On 9 Oct 2026 it ran from 98 % to 61 % in
two hours with live on it and no rule looked. The watcher's container cannot
see a battery (`/sys/class/power_supply` is empty inside the Docker VM), so
the reading comes from the host:

- `scripts/ops/power-watch.ps1` — a task of the host, "SRAN power watch" —
  writes what Windows says about the power to `power_last.json` in the backup
  folder once a minute (whether there is a battery, whether the machine is on
  it, the charge, the minutes left). Register it once, as the owner:
  `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\ops\power-register-task.ps1`.
  It is registered to start on battery and not to be stopped when the charger
  is pulled — Task Scheduler's defaults are the other way round — and a second
  trigger starts it again within five minutes if it has died.
- **On battery** for two minutes: said once, at warning priority ("The host is
  on battery: 73% left") — carrying the machine to another socket says nothing.
- **Charge at or under 40 %, or an hour or less left**: a critical page, at
  once, in the same incident — "Battery at 38% — plug the host in now". The two
  thresholds are `OPS_WATCH_POWER_PAGE_PCT` and `OPS_WATCH_POWER_PAGE_MIN`.
- **The reading stopped** (older than ten minutes, for five minutes): a
  warning — the watcher would not see the machine go on battery. A host where
  the task was never registered, and a machine with no battery, are quiet.
- Back on mains power the incident closes with the usual "Recovered" note.

What it cannot do: page after the battery is empty, or while the machine
sleeps. The daily smoke task had the same two defaults (it would not have
started at 06:30 on a morning the laptop was left unplugged);
`scripts/smoke-register-task.ps1` now sets both, and the task has to be
registered again for that to take effect.

**Nothing heavy starts on battery.** The page above tells a person; it does not
stop a tool. On battery the machine runs at about half speed (the backend lane:
510 s against 321 s on mains), and a deploy the battery cuts short leaves live
between two images. Until 12 Oct 2026 the rule was a line to read — the power
was printed before a lane, it said "battery", and the lane ran. So the tools
ask for themselves: `scripts/ops/power.sh need "<what>"` exits 0 on mains (and
on a machine with no battery) and says `REFUSED`, exit 3, on battery or when
the power cannot be read. The backend deploy (`scripts/deploy-backend.sh`) and
the image build (`scripts/build-images.sh`) ask it before they touch anything.
To run one of them on battery anyway, say so on that one command:

```bash
RUN_ON_BATTERY=yes scripts/deploy-backend.sh --image ai-gateway-backend:1a2b3c4
```

The word is `yes` and nothing else; it is read from the command's own
environment and from no file, and the command says on its first line that it
is running on battery because it was told to. There is no setting that says
"this is mains". What is not refused: the daily smoke (it is the monitor, and
must run on a morning the laptop was left unplugged), one test file run by
hand, and a `--dry-run`. `scripts/tests/test_power_on_battery.py` holds it.

Why the turn rules live here as well as in `alert_rules`: until FIX130 the
gateway's `request_logs` only recorded requests that reached a provider. On
the test stack, 12 turns with every model dead produced 12 failed assistant
rows in the chat database and **0** `request_logs` rows — so the backend's
`no_successful_turn` / `error_rate` rules would have stayed silent. The chat
database is where a turn's outcome is truthfully recorded (web and desktop),
and the ops watcher reads it from outside the backend.

Since FIX130 E3 a turn that fails before any provider is called writes one
`request_logs` row too: `status = 'pre_provider_error'`, `provider = ''`,
`error_code` = the error's code (`model_not_served`, `no_capable_model`,
`no_allowed_provider`, `provider_key_missing`, `upstream_timeout`,
`internal_error`, …), zero tokens and cost. `error_rate` and
`no_successful_turn` count it; `provider_failure` (`status = 'error'`) does
not. Admission and access refusals (`rate_limit_exceeded`,
`quota_exceeded`, `authentication_error`, `authorization_error`) write no row
— the system worked and each has its own ledger — and policy blocks keep
their single `blocked` row (`app/services/pre_provider_log.py`). Failures
AFTER a provider was called on the non-streaming path still write no row.

It runs from the backend image with the docker socket read-only; it is not
part of the test stack. `python -m app.ops.watch once` runs a single tick
(the test-fire recipe in `scripts/probes/FIX129/`).

## Sign-out ends the session (B2)

`POST /portal/auth/logout` revokes the refresh token (as before) AND the
access token's `jti` (`portal_access_revoked:<jti>` in the Portal's Redis,
checked by `get_portal_user`), then calls chat-edge's
`POST /internal/auth/revoke` (gated by `INTERNAL_SERVICE_SECRET`,
`CHAT_EDGE_INTERNAL_URL`), which keeps its own list (`auth:revoked:jti:*`,
`auth:revoked:user:*` in its Redis) and answers 401 from the next request.
The web client polls `GET /api/v1/auth/session` every 5 s while the tab is
visible, so a signed-out or copied token is off every open tab within
seconds. Both revocation checks fail OPEN on a Redis outage (availability
first, logged as `access_token_revocation_check_failed` /
`auth_revocation_check_failed`).

FIX130 — the Portal's middleware used to count "a portal cookie is present"
as signed in, so a device holding a revoked token looped login → chat →
login. It now asks the backend (`GET /portal/me`, then
`POST /portal/auth/refresh`, 1.5 s each, `portal/lib/session-check.ts`):
signed in → as before (an expired access token with a valid refresh token
is signed in, the rotated cookies ride the response); rejected → the login
page renders with the stale cookies cleared; backend unreachable → the login
page renders (never a redirect, so never a loop) and app pages render (their
API calls are checked by the backend). A deleted chat's files answer 404 on
`/files/{id}/download`, `/text` and `/versions` while the chat is deleted
(B1, `file_repo.go`).

## autoheal: a watchdog that acts on "unhealthy" (A3)

`autoheal` (`willfarrell/autoheal:1.2.0`, in `docker-compose.yml`) polls the
Docker daemon every 10 s and restarts any container labelled `autoheal=true`
whose health is `unhealthy` (graceful stop, 20 s; 60 s grace after autoheal
itself starts). Labelled: `backend`, `gateway` (base file), `chat-edge-api`,
`web-chat` (webchat file) — the services that have a healthcheck. With the
backend's healthcheck (`/ready`, 10 s interval, 3 retries) the wedge above
would have ended in about 40 s instead of 34 minutes (and the stuck-transaction check in /ready makes the wedge itself a red healthcheck).

It needs the docker socket (= host control). It is therefore NOT part of the
test stack: `scripts/test_stack_gen.py` allowlists services and refuses any
socket mount.

See it working:

```sh
docker logs ai-gateway-autoheal-1          # "… (unhealthy) found, restarting …" lines
docker ps --filter label=autoheal=true     # who is watched
```

A restart by autoheal looks, from the service's side, like any restart:
`docker inspect <c> --format '{{.RestartCount}} {{.State.StartedAt}}'`
moves. If a container is restarted repeatedly the healthcheck is telling you
something the restart does not fix — read the container log, not autoheal's.

## Backups and the restore drill (A5)

`docs/ops/backups.md`: daily 03:15 Asia/Bangkok, both databases + uploads,
every backup restored into a scratch database and counted, alerts on
failure via ntfy / Resend.

## Log secret scan

`scripts/probes/FIX129/log_secret_scan.py` fails if any secret value from a
`.env` appears in a container log:

```sh
python scripts/probes/FIX129/log_secret_scan.py --env .env                 # whole history of every ai-gateway container
python scripts/probes/FIX129/log_secret_scan.py --env .env --since 24h     # since the last day
python scripts/probes/FIX129/log_secret_scan.py --env .env --project sran-test
```

* Secrets = the values of every key whose NAME matches
  `(PASSWORD|SECRET|KEY|TOKEN|DSN)`, plus the password part of ANY value that
  is a URL with `user:pass@` or a libpq DSN with `password=…`, plus (FIX130)
  the path of `ALERT_NTFY_URL` / `*WEBHOOK_URL` — the topic. Values shorter
  than 8 characters and the well-known dev defaults (`postgres`, `changeme`,
  `…change-in-prod…`) are ignored. The 2026-09-23 FIX130 run: green on all
  22 live and 13 test-stack containers, topic included.
* It runs `docker logs` for every container whose
  `com.docker.compose.project` label is the project (running or stopped) and
  reports, per container, the KEY NAMES found and the counts — never a value,
  never the line.
* Exit 1 on any hit, 0 when clean, 2 on a usage error. Read-only.

The 2026-09-23 run against the live project
(`scripts/probes/FIX129/results/ops/log_secret_scan_live.txt`) was the
evidence for finding B3:

```
  RED   ai-gateway-rate-limiter-1: REDIS_PASSWORD x2 (first at log line 1)
  ok    (the other 18 containers)
RESULT: RED - 2 secret occurrence(s) in container logs
```

The rate-limiter's boot line logged `REDIS_URL` whole
(`redis://:<password>@redis:6379`). Fixed in `rate-limiter/src/main.rs`
(`redact_url`: `redis://:***@redis:6379`, unit-tested); the scan goes green
for that container once the rebuilt image is deployed — the old log lines
stay in the container's log until it is recreated, so expect the scan to
stay RED on `--since` windows that include the old boot until then.

The other services were checked for the same pattern:

| Service | Boot log | Result |
|---|---|---|
| `log-worker` | `log-worker/main.go:56-57` logs the DSN through `maskDSN` (`log-worker/main.go:191`), which overwrites the `password=` value with `*` | masked (B5) |
| `dlp-sidecar` | `dlp-sidecar/src/main.rs:209-214` logs ports and detector count only | nothing to leak |
| `gateway` (Go) | `services/gateway/cmd/server/main.go` parses `DATABASE_URL` and logs only `err` on failure | nothing logged |
| `chat-edge-api` | no boot line prints `DATABASE_URL` / `REDIS_URL`; the scan found 0 hits in 1.8 MB of log | clean |

Run the scan after every deploy that touches a boot log, and before sharing a
`docker logs` capture with anyone.
