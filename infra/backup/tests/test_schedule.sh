#!/usr/bin/env bash
# FIX131 B — the backup scheduler: decided by the WALL clock, catches up after
# a missed window, never runs twice, retries a failure with backoff, and
# records the last success / failure (last_success.json, last_failure.json).
#
# NIGHT3: the loop computed "next 03:15" once and slept; Docker Desktop's VM
# clock stops while the PC sleeps, so the 03:15 run fired at 05:52, and a
# window missed while the container was down was skipped altogether.
#
# Runs INSIDE the backup image (real entrypoint.sh / schedule.sh / lib.sh, GNU
# date with tzdata, jq). backup.sh / drill.sh / offsite.sh / notify.sh are
# stubs (BACKUP_SCRIPTS_DIR) — no database, no network, no alert. The clock is
# faked with BACKUP_FAKE_NOW; each "tick" is one `entrypoint.sh tick`:
#
#   docker build -t ai-gateway-backup:fix131-wip infra/backup
#   docker run --rm -v "$PWD/infra/backup/tests:/tests:ro" --entrypoint bash \
#     ai-gateway-backup:fix131-wip /tests/test_schedule.sh </dev/null
#
# Exit 0 = every check passed. Prints one PASS/FAIL line per check.
set -uo pipefail
export TZ=Asia/Bangkok BACKUP_AT=03:15
unset BACKUP_OFFSITE_REPOSITORY ALERT_NTFY_URL ALERT_EMAIL_TO RESEND_API_KEY || true

T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
STUB="$T/stub"
mkdir -p "$STUB"

cat > "$STUB/backup.sh" <<'EOF'
#!/usr/bin/env bash
echo "backup $BACKUP_FAKE_NOW" >> "$CALLS"
[ "${STUB_BACKUP_RC:-0}" = 0 ] || { echo "stub: pg_dump failed" >&2; exit 1; }
id=$(date -d "@$BACKUP_FAKE_NOW" +%Y%m%d_%H%M%S)
d="$BACKUP_ROOT/$id"; mkdir -p "$d"
jq -n --arg id "$id" --arg at "$(date -u -d "@$BACKUP_FAKE_NOW" +%Y-%m-%dT%H:%M:%SZ)" \
  '{schema: 1, id: $id, created_at: $at,
    files: {"gateway.dump": {bytes: 2000000, sha256: "a"}, "sran_ai.dump": {bytes: 300000, sha256: "b"},
            "uploads.tar.gz": {bytes: 5000000, sha256: "c"}},
    uploads: {entries: 3}}' > "$d/manifest.json"
printf '%s\n' "$d" > "$BACKUP_RESULT_FILE"
EOF
cat > "$STUB/drill.sh" <<'EOF'
#!/usr/bin/env bash
echo "drill $1" >> "$CALLS"
if [ "${STUB_DRILL_RC:-0}" = 0 ]; then
  echo '{"pass": true, "checks_total": 14, "checks_failed": 0, "checks": []}' > "$1/drill.json"; exit 0
fi
echo '{"pass": false, "checks_total": 14, "checks_failed": 1, "checks": [{"name": "rows:gw:request_logs", "pass": false}]}' > "$1/drill.json"
exit 1
EOF
cat > "$STUB/offsite.sh" <<'EOF'
#!/usr/bin/env bash
echo "offsite $2" >> "$CALLS"
case "${STUB_OFFSITE:-none}" in
  none) echo '{"configured": false}' > "$2/offsite.json"; exit 0 ;;
  pass) echo '{"configured": true, "pass": true, "snapshot": "1a2b3c4d5e6f", "checks": []}' > "$2/offsite.json"; exit 0 ;;
  fail) echo '{"configured": true, "pass": false, "failed_step": "connect", "checks": [{"name": "connect", "pass": false, "actual": "wrong repository password"}]}' > "$2/offsite.json"; exit 1 ;;
esac
EOF
cat > "$STUB/notify.sh" <<'EOF'
#!/usr/bin/env bash
printf 'notify|%s|%s\n' "$1" "$2" >> "$NOTIFY_LOG"
EOF
chmod +x "$STUB"/*.sh

fails=0
pass() { printf 'PASS %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1"; fails=$((fails + 1)); }
check() { if eval "$2"; then pass "$1"; else fail "$1"; fi; }

ROOT="" CALLS="" NOTIFY_LOG="" OUT=""
fresh() {  # a new, empty backup root
  ROOT="$T/root.$1"; mkdir -p "$ROOT"
  CALLS="$T/calls.$1"; NOTIFY_LOG="$T/notify.$1"; OUT="$T/out.$1"
  : > "$CALLS"; : > "$NOTIFY_LOG"; : > "$OUT"
}
ep() { date -d "$1" +%s; }
seed_success() {  # seed_success "<local time>" — a last_success.json finished then
  jq -n --argjson e "$(ep "$1")" '{schema: 1, epoch: $e, backup_id: "seed", drill: {pass: true}, offsite: {status: "seed"}}' \
    > "$ROOT/last_success.json"
}
tick() {  # tick "<local time>" [VAR=value ...] — one scheduler decision at that wall-clock time
  local at="$1"; shift
  env "$@" BACKUP_FAKE_NOW="$(ep "$at")" BACKUP_ROOT="$ROOT" BACKUP_SCRIPTS_DIR="$STUB" UPLOADS_DIR="$T" \
    CALLS="$CALLS" NOTIFY_LOG="$NOTIFY_LOG" /entrypoint.sh tick >> "$OUT" 2>&1
}
runs() { grep -c '^backup ' "$CALLS"; }
pages() { grep -c '^notify|high|' "$NOTIFY_LOG"; }
js() { jq -r "$1" "$2" 2>/dev/null; }

# 1. Not due: tonight's backup already succeeded.
fresh notdue
seed_success "2026-09-24 03:17"
tick "2026-09-24 10:00"
check "not due: no run when the newest success is after the latest slot" '[ "$(runs)" = 0 ]'
check "not due: the decision is logged (wait + next slot)" 'grep -q "decision: wait .*next slot 2026-09-25T03:15:00+0700" "$OUT"'

# 2. Due at the slot → one run, recorded; the next tick does not run again.
fresh due
seed_success "2026-09-23 03:17"
tick "2026-09-24 03:15:20"
check "due: the slot runs the backup once" '[ "$(runs)" = 1 ]'
check "due: logged as a scheduled run" 'grep -q "decision: run (schedule, attempt 1)" "$OUT"'
S="$ROOT/last_success.json"
check "record: last_success.json written by this run" '[ -s "$S" ] && [ "$(js .backup_id "$S")" != seed ]'
check "record: epoch = the finish time" '[ "$(js .epoch "$S")" = "$(ep "2026-09-24 03:15:20")" ]'
check "record: time in UTC" '[ "$(js .at "$S")" = "2026-09-23T20:15:20Z" ]'
check "record: backup id" '[ "$(js .backup_id "$S")" = "20260924_031520" ]'
check "record: drill pass + checks" '[ "$(js ".drill.pass, .drill.checks_total" "$S" | tr "\n" " ")" = "true 14 " ]'
check "record: sizes per file + total" '[ "$(js ".sizes[\"gateway.dump\"], .sizes.total" "$S" | tr "\n" " ")" = "2000000 7300000 " ]'
check "record: off-site result carried (not configured)" '[ "$(js .offsite.status "$S")" = "not_configured" ]'
check "record: slot + trigger" '[ "$(js ".slot, .trigger" "$S" | tr "\n" " ")" = "2026-09-24T03:15:00+0700 schedule " ]'
check "record: written atomically (no temp file left)" '! ls -A "$ROOT" | grep -q "\.tmp\."'
check "lock: released after the run" '[ ! -e "$ROOT/.backup.lock" ]'
tick "2026-09-24 03:16:20"
tick "2026-09-24 09:00"
check "no double run: later ticks the same day do not run again" '[ "$(runs)" = 1 ]'
check "success: no page (BACKUP_NOTIFY_SUCCESS=0)" '[ ! -s "$NOTIFY_LOG" ]'

# 3. Catch-up: the PC slept through 03:15 (NIGHT3: it fired at 05:52).
fresh catchup
seed_success "2026-09-23 03:17"
tick "2026-09-24 05:52"
check "catch-up: a missed slot runs at the first tick after wake-up" '[ "$(runs)" = 1 ]'
check "catch-up: logged as a catch-up with how late" 'grep -q "decision: run (catch-up, attempt 1) — slot 2026-09-24T03:15:00+0700 passed 2 h 37 min ago and was missed" "$OUT"'
check "catch-up: recorded as trigger catch-up" '[ "$(js .trigger "$ROOT/last_success.json")" = "catch-up" ]'
tick "2026-09-24 05:53"
check "catch-up: once" '[ "$(runs)" = 1 ]'

# 3b. Container down for four days: ONE catch-up run, not four.
fresh down
seed_success "2026-09-20 03:17"
tick "2026-09-24 09:00"
tick "2026-09-24 09:01"
tick "2026-09-24 09:02"
check "down 4 days: exactly one catch-up run" '[ "$(runs)" = 1 ]'

# 4. No double run: another backup (run --rm --no-deps backup now) holds the lock.
fresh locked
seed_success "2026-09-23 03:17"
mkdir -p "$ROOT/.backup.lock"
jq -n --argjson at "$(ep "2026-09-24 03:13")" '{host: "other-container", pid: 7, at: $at}' > "$ROOT/.backup.lock/owner.json"
tick "2026-09-24 03:15:30"
check "lock held: no second backup starts" '[ "$(runs)" = 0 ]'
check "lock held: the skip is logged with the holder" 'grep -q "decision: skip — another backup is running (other-container pid 7" "$OUT"'
check "lock held: the other run's lock is left alone" '[ -d "$ROOT/.backup.lock" ]'
tick "2026-09-24 06:30"
check "stale lock (> 3 h): taken over and the backup runs" '[ "$(runs)" = 1 ] && grep -q "lock: taking over a stale lock" "$OUT"'
fresh nowlocked
mkdir -p "$ROOT/.backup.lock"
jq -n --argjson at "$(date +%s)" '{host: "loop", pid: 1, at: $at}' > "$ROOT/.backup.lock/owner.json"
env BACKUP_ROOT="$ROOT" BACKUP_SCRIPTS_DIR="$STUB" UPLOADS_DIR="$T" CALLS="$CALLS" NOTIFY_LOG="$NOTIFY_LOG" \
  /entrypoint.sh now >> "$OUT" 2>&1
rc=$?
check "now while a backup runs: refused (exit 4), nothing started" '[ "$rc" = 4 ] && [ "$(runs)" = 0 ]'

# 5. Failure → retry with backoff; paged once (the transition), not per retry.
fresh retry
seed_success "2026-09-23 03:17"
tick "2026-09-24 03:15:30" STUB_BACKUP_RC=1
F="$ROOT/last_failure.json"
check "failure: recorded (rc 1, attempt 1)" '[ "$(js ".rc, .attempts, .status" "$F" | tr "\n" " ")" = "1 1 BACKUP FAILED " ]'
check "failure: next retry in 10 min" '[ "$(js .next_retry_epoch "$F")" = "$(ep "2026-09-24 03:25:30")" ]'
check "failure: the first failure is paged by this container" '[ "$(pages)" = 1 ] && [ "$(js .notified "$F")" = true ]'
check "failure: the page says what is wrong in words" 'grep -q "notify|high|Backup failed: no new backup was written" "$NOTIFY_LOG"'
check "failure: no success record written" '[ "$(js .backup_id "$ROOT/last_success.json")" = "seed" ]'
tick "2026-09-24 03:20:30" STUB_BACKUP_RC=1
check "retry: not before the backoff" '[ "$(runs)" = 1 ] && grep -q "decision: retry-wait — attempt 1 for the slot" "$OUT"'
tick "2026-09-24 03:25:40" STUB_BACKUP_RC=1
check "retry: after 10 min the second attempt runs" '[ "$(runs)" = 2 ] && grep -q "decision: run (retry, attempt 2)" "$OUT"'
check "retry: attempt 2 recorded, next in 20 min" '[ "$(js .attempts "$F")" = 2 ] && [ "$(js .next_retry_epoch "$F")" = "$(ep "2026-09-24 03:45:40")" ]'
check "retry: a repeated failure is not paged again" '[ "$(pages)" = 1 ] && [ "$(js .notified "$F")" = false ]'
tick "2026-09-24 03:40:00" STUB_BACKUP_RC=1
check "retry: backoff doubles (no run at +14 min)" '[ "$(runs)" = 2 ]'
tick "2026-09-24 03:46:00"
check "retry: the third attempt succeeds and is recorded as a retry" '[ "$(runs)" = 3 ] && [ "$(js ".trigger, .attempt" "$ROOT/last_success.json" | tr "\n" " ")" = "retry 3 " ]'
tick "2026-09-24 03:50:00"
check "retry: done — no more runs after the success" '[ "$(runs)" = 3 ]'
check "backoff is capped (BACKUP_RETRY_MAX_S)" '[ "$(bash -c ". /lib.sh; . /schedule.sh; retry_backoff 9")" = 7200 ]'

# 6. No last_success.json yet (backups made before FIX131): the newest proven
#    directory counts; a directory whose drill failed does not.
fresh fallback
mkdir -p "$ROOT/20260924_031600"
echo '{"created_at": "2026-09-23T20:16:00Z"}' > "$ROOT/20260924_031600/manifest.json"
echo '{"pass": true}' > "$ROOT/20260924_031600/drill.json"
tick "2026-09-24 09:00"
check "fallback: a proven backup directory from tonight = not due" '[ "$(runs)" = 0 ]'
echo '{"pass": false}' > "$ROOT/20260924_031600/drill.json"
tick "2026-09-24 09:01"
check "fallback: a directory whose drill failed does not count = due" '[ "$(runs)" = 1 ]'

# 7. The newest success is already stale (> 26 h): ops-watch has paged "no
#    fresh backup" (or will within a tick) — this container does not page too.
fresh stale
seed_success "2026-09-22 03:17"
tick "2026-09-24 03:15:30" STUB_BACKUP_RC=1
check "stale: a failure after 26 h is not paged by this container" '[ "$(pages)" = 0 ] && [ "$(js .notified "$ROOT/last_failure.json")" = false ]'
check "stale: the log says who owns it" 'grep -q "ops-watch owns the open incident" "$OUT"'

# 8. Drill failed (rc 2).
fresh drill
seed_success "2026-09-23 03:17"
tick "2026-09-24 03:15:30" STUB_DRILL_RC=1
check "drill failed: recorded as DRILL FAILED, paged in words" '[ "$(js ".rc, .status" "$ROOT/last_failure.json" | tr "\n" " ")" = "2 DRILL FAILED " ] && grep -q "notify|high|Backup failed its restore test" "$NOTIFY_LOG"'

# 9. Off-site copy failed (rc 3): the local backup is fresh (a success record
#    carrying the off-site failure); paged on the first night only.
fresh offsite
seed_success "2026-09-23 03:17"
tick "2026-09-24 03:15:30" STUB_OFFSITE=fail
S="$ROOT/last_success.json"
check "off-site failed: success record carries offsite fail + notified" '[ "$(js ".offsite.status, .offsite.notified" "$S" | tr "\n" " ")" = "fail true " ]'
check "off-site failed: paged in words" 'grep -q "notify|high|Off-site backup copy failed (the local backup is fine)" "$NOTIFY_LOG"'
check "off-site failed: the local backup counts (no retry)" '[ ! -e "$ROOT/last_failure.json" ]'
tick "2026-09-25 03:15:30" STUB_OFFSITE=fail
check "off-site still failing next night: not paged again" '[ "$(pages)" = 1 ] && [ "$(js .offsite.notified "$S")" = false ]'
tick "2026-09-26 03:15:30" STUB_OFFSITE=pass
check "off-site back: recorded pass + snapshot" '[ "$(js ".offsite.status, .offsite.snapshot" "$S" | tr "\n" " ")" = "pass 1a2b3c4d5e6f " ]'

echo "---"
if [ "$fails" -eq 0 ]; then echo "test_schedule: ALL PASS"; exit 0; fi
echo "test_schedule: $fails FAILED"
exit 1
