#!/usr/bin/env bash
# FIX131 B — the backup scheduler's decisions and records (docs/ops/backups.md).
# Sourced by entrypoint.sh (and by infra/backup/tests/test_schedule.sh), after
# lib.sh. Not executed.
#
# Why: until FIX131 the loop computed "next 03:15" once and slept that many
# seconds. Docker Desktop's VM clock does not advance while the PC sleeps, so
# the 03:15 run due on 2026-09-24 fired at 05:52 (2 h 37 min late), and a run missed
# while the container was down was simply skipped. Now the loop wakes every
# BACKUP_TICK_S (60 s) and compares the WALL clock with the schedule:
#
#   slot  = the latest BACKUP_AT (in $TZ) that has passed
#   due   = the newest SUCCESSFUL backup finished before that slot
#
# so after a missed window (PC asleep, container down, a failure) it runs as
# soon as it is back — once, not once per missed day. A failed run is retried
# with backoff (BACKUP_RETRY_BASE_S doubling up to BACKUP_RETRY_MAX_S). Two
# runs never overlap: a lock directory in the backup root (mkdir is atomic on
# every filesystem, including the Windows bind mount; flock is not reliable
# there), shared by the scheduled loop and `run --rm backup now`.
#
# Records (written atomically: temp file in the same directory + mv):
#   $BACKUP_ROOT/last_success.json  the newest backup whose restore drill
#                                   passed: time, id, drill, sizes, off-site
#   $BACKUP_ROOT/last_failure.json  the newest failed run: rc, step, attempt,
#                                   next retry, whether this container paged
# ops-watch reads both (read-only mount) and pages when there is no fresh
# backup — the backup container cannot report its own death.

BACKUP_TICK_S="${BACKUP_TICK_S:-60}"
BACKUP_RETRY_BASE_S="${BACKUP_RETRY_BASE_S:-600}"
BACKUP_RETRY_MAX_S="${BACKUP_RETRY_MAX_S:-7200}"
BACKUP_MAX_AGE_H="${BACKUP_MAX_AGE_H:-26}"
BACKUP_LOCK_STALE_S="${BACKUP_LOCK_STALE_S:-10800}"

SUCCESS_FILE="$BACKUP_ROOT/last_success.json"
FAILURE_FILE="$BACKUP_ROOT/last_failure.json"
LOCK_DIR="$BACKUP_ROOT/.backup.lock"

# The wall clock. BACKUP_FAKE_NOW (epoch seconds) is for the tests only.
now_epoch() {
  if [ -n "${BACKUP_FAKE_NOW:-}" ]; then echo "$BACKUP_FAKE_NOW"; else date +%s; fi
}

fmt_time() { date -d "@$1" +%Y-%m-%dT%H:%M:%S%z; }
fmt_utc() { date -u -d "@$1" +%Y-%m-%dT%H:%M:%SZ; }

# "2 h 37 min" / "45 s"
fmt_age() {
  local s="$1"
  [ "$s" -lt 0 ] && s=0
  if [ "$s" -lt 60 ]; then echo "${s} s"
  elif [ "$s" -lt 3600 ]; then echo "$((s / 60)) min"
  else echo "$((s / 3600)) h $(((s % 3600) / 60)) min"; fi
}

# The latest scheduled time at or before epoch $1 (BACKUP_AT in $TZ).
slot_at_or_before() {
  local now="$1" day s
  day=$(date -d "@$now" +%Y-%m-%d)
  s=$(date -d "$day $BACKUP_AT" +%s)
  if [ "$s" -gt "$now" ]; then
    day=$(date -d "$day -1 day" +%Y-%m-%d)
    s=$(date -d "$day $BACKUP_AT" +%s)
  fi
  echo "$s"
}

# The first scheduled time after epoch $1.
slot_after() {
  local now="$1" day s
  day=$(date -d "@$now" +%Y-%m-%d)
  s=$(date -d "$day $BACKUP_AT" +%s)
  if [ "$s" -le "$now" ]; then
    day=$(date -d "$day +1 day" +%Y-%m-%d)
    s=$(date -d "$day $BACKUP_AT" +%s)
  fi
  echo "$s"
}

# The newest proven backup directory (drill.json pass) — the fallback when
# there is no last_success.json yet (backups written before FIX131).
# Prints "<epoch> <dir>" or nothing.
newest_proven_dir() {
  local d at e
  for d in $(ls -1d "$BACKUP_ROOT"/[0-9]*_[0-9]* 2>/dev/null | grep -Ev '\.failed$' | sort -r); do
    [ -f "$d/manifest.json" ] && [ -f "$d/drill.json" ] || continue
    jq -e '.pass == true' "$d/drill.json" >/dev/null 2>&1 || continue
    at=$(jq -r '.created_at // empty' "$d/manifest.json" 2>/dev/null)
    [ -n "$at" ] || continue
    e=$(date -d "$at" +%s 2>/dev/null) || continue
    echo "$e $d"
    return 0
  done
}

# Epoch of the newest successful backup (0 = none).
last_success_epoch() {
  local e p
  if [ -s "$SUCCESS_FILE" ] && e=$(jq -er '.epoch | numbers' "$SUCCESS_FILE" 2>/dev/null); then
    echo "$e"; return 0
  fi
  p=$(newest_proven_dir)
  if [ -n "$p" ]; then echo "${p%% *}"; else echo 0; fi
}

# decide <now> — sets DECISION (run | wait | retry-wait), TRIGGER (schedule |
# catch-up | retry), SLOT, NEXT_AT, ATTEMPT (of this slot, for a run) and
# REASON (one human line for the log).
decide() {
  local now="$1" last f_slot f_attempts f_epoch backoff retry_at
  SLOT=$(slot_at_or_before "$now")
  last=$(last_success_epoch)
  LAST_OK="$last"
  if [ "$last" -ge "$SLOT" ]; then
    DECISION=wait; TRIGGER=""; ATTEMPT=0
    NEXT_AT=$(slot_after "$now")
    REASON="newest successful backup $(fmt_time "$last") is not older than the slot $(fmt_time "$SLOT"); next slot $(fmt_time "$NEXT_AT")"
    return 0
  fi
  ATTEMPT=1
  if [ $((now - SLOT)) -lt $((2 * BACKUP_TICK_S)) ]; then TRIGGER=schedule; else TRIGGER=catch-up; fi
  if [ -s "$FAILURE_FILE" ]; then
    f_slot=$(jq -r '.slot_epoch // 0' "$FAILURE_FILE" 2>/dev/null || echo 0)
    f_attempts=$(jq -r '.attempts // 1' "$FAILURE_FILE" 2>/dev/null || echo 1)
    f_epoch=$(jq -r '.epoch // 0' "$FAILURE_FILE" 2>/dev/null || echo 0)
    if [ "$f_slot" = "$SLOT" ] && [ "$f_epoch" -gt "$last" ]; then
      backoff=$(retry_backoff "$f_attempts")
      retry_at=$((f_epoch + backoff))
      ATTEMPT=$((f_attempts + 1)); TRIGGER=retry
      if [ "$now" -lt "$retry_at" ]; then
        DECISION=retry-wait; NEXT_AT="$retry_at"
        REASON="attempt $f_attempts for the slot $(fmt_time "$SLOT") failed at $(fmt_time "$f_epoch"); retry $ATTEMPT at $(fmt_time "$retry_at") (backoff $(fmt_age "$backoff"))"
        return 0
      fi
    fi
  fi
  DECISION=run; NEXT_AT="$now"
  local newest="none yet"
  [ "$last" -gt 0 ] && newest="$(fmt_time "$last") ($(fmt_age $((now - last))) old)"
  case "$TRIGGER" in
    schedule) REASON="slot $(fmt_time "$SLOT") reached; newest successful backup $newest" ;;
    catch-up) REASON="slot $(fmt_time "$SLOT") passed $(fmt_age $((now - SLOT))) ago and was missed (PC asleep or container down); newest successful backup $newest" ;;
    retry)    REASON="retry $ATTEMPT for the slot $(fmt_time "$SLOT"); newest successful backup $newest" ;;
  esac
}

# Seconds to wait after failed attempt number $1 of a slot:
# base, 2×base, 4×base … capped at max.
retry_backoff() {
  local n="$1" b="$BACKUP_RETRY_BASE_S" i=1
  while [ "$i" -lt "$n" ] && [ "$b" -lt "$BACKUP_RETRY_MAX_S" ]; do b=$((b * 2)); i=$((i + 1)); done
  [ "$b" -gt "$BACKUP_RETRY_MAX_S" ] && b="$BACKUP_RETRY_MAX_S"
  echo "$b"
}

# ---------------------------------------------------------------------------
# The lock: one backup at a time across the scheduled container and any
# `run --rm backup now`. A lock older than BACKUP_LOCK_STALE_S (a run takes
# minutes) is a crashed run's and is taken over, with a log line.
# ---------------------------------------------------------------------------
LOCK_HELD=0
LOCK_HOLDER=""

lock_acquire() {
  local now at age
  now=$(now_epoch)
  if mkdir "$LOCK_DIR" 2>/dev/null; then
    jq -n --arg host "$(hostname)" --argjson pid "$$" --argjson at "$now" '{host: $host, pid: $pid, at: $at}' \
      > "$LOCK_DIR/owner.json" 2>/dev/null || true
    LOCK_HELD=1
    return 0
  fi
  at=$(jq -r '.at // empty' "$LOCK_DIR/owner.json" 2>/dev/null)
  [ -n "$at" ] || at=$(stat -c %Y "$LOCK_DIR" 2>/dev/null || echo "$now")
  age=$((now - at))
  LOCK_HOLDER="$(jq -r '"\(.host) pid \(.pid)"' "$LOCK_DIR/owner.json" 2>/dev/null || echo unknown), since $(fmt_age "$age")"
  if [ "$age" -ge "$BACKUP_LOCK_STALE_S" ]; then
    log "lock: taking over a stale lock ($LOCK_HOLDER; stale after $(fmt_age "$BACKUP_LOCK_STALE_S"))"
    rm -rf "$LOCK_DIR"
    if mkdir "$LOCK_DIR" 2>/dev/null; then
      jq -n --arg host "$(hostname)" --argjson pid "$$" --argjson at "$now" '{host: $host, pid: $pid, at: $at}' \
        > "$LOCK_DIR/owner.json" 2>/dev/null || true
      LOCK_HELD=1
      return 0
    fi
  fi
  return 1
}

lock_release() {
  if [ "$LOCK_HELD" = 1 ]; then rm -rf "$LOCK_DIR"; LOCK_HELD=0; fi
}

# At scheduler start: a lock left by THIS container's previous life (same
# hostname = same container, restarted mid-run) is released at once instead
# of blocking backups until it goes stale.
lock_reclaim_own() {
  local host
  [ -d "$LOCK_DIR" ] || return 0
  host=$(jq -r '.host // empty' "$LOCK_DIR/owner.json" 2>/dev/null)
  if [ -n "$host" ] && [ "$host" = "$(hostname)" ]; then
    log "lock: released the lock this container held before it restarted"
    rm -rf "$LOCK_DIR"
  fi
}

# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

# atomic_write <file> — stdin → <file>, via a temp file in the same directory.
atomic_write() {
  local f="$1" tmp
  tmp="$(dirname "$f")/.$(basename "$f").tmp.$$"
  if cat > "$tmp" && mv -f "$tmp" "$f"; then return 0; fi
  rm -f "$tmp"
  log "WARN could not write $f"
  return 1
}

# write_success_record <dir> <started_epoch> <trigger> <attempt> <offsite_status> <notified 0|1>
write_success_record() {
  local dir="$1" started="$2" trigger="$3" attempt="$4" off_status="$5" notified="$6" now
  now=$(now_epoch)
  local manifest="$dir/manifest.json" drill="$dir/drill.json" offsite="$dir/offsite.json"
  jq -n \
    --arg at "$(fmt_utc "$now")" --argjson epoch "$now" \
    --arg started_at "$(fmt_utc "$started")" --argjson started_epoch "$started" \
    --arg slot "$(fmt_time "$SLOT")" --argjson slot_epoch "$SLOT" \
    --arg dir "$dir" --arg id "$(basename "$dir")" \
    --arg trigger "$trigger" --argjson attempt "$attempt" \
    --arg off_status "$off_status" --argjson notified "$([ "$notified" = 1 ] && echo true || echo false)" \
    --argjson manifest "$(cat "$manifest" 2>/dev/null || echo '{}')" \
    --argjson drill "$(cat "$drill" 2>/dev/null || echo '{}')" \
    --argjson offsite "$(cat "$offsite" 2>/dev/null || echo '{}')" \
    '{
       schema: 1, at: $at, epoch: $epoch, started_at: $started_at, started_epoch: $started_epoch,
       slot: $slot, slot_epoch: $slot_epoch, trigger: $trigger, attempt: $attempt,
       backup_id: $id, dir: $dir, duration_s: ($epoch - $started_epoch),
       drill: {pass: ($drill.pass // false), checks_total: ($drill.checks_total // 0), checks_failed: ($drill.checks_failed // 0)},
       sizes: ((($manifest.files // {}) | with_entries(.value = .value.bytes)) | . + {total: ([.[]] | add // 0)}),
       offsite: {status: $off_status, snapshot: ($offsite.snapshot // null), notified: $notified}
     }' 2>/dev/null | atomic_write "$SUCCESS_FILE"
}

# write_failure_record <rc> <status> <dir|""> <started_epoch> <attempt> <notified 0|1> <summary>
write_failure_record() {
  local rc="$1" status="$2" dir="$3" started="$4" attempt="$5" notified="$6" summary="$7" now next
  now=$(now_epoch)
  next=$((now + $(retry_backoff "$attempt")))
  jq -n \
    --arg at "$(fmt_utc "$now")" --argjson epoch "$now" --argjson started_epoch "$started" \
    --arg slot "$(fmt_time "$SLOT")" --argjson slot_epoch "$SLOT" \
    --argjson rc "$rc" --arg status "$status" --arg dir "$dir" \
    --argjson attempts "$attempt" --argjson next "$next" --arg next_at "$(fmt_utc "$next")" \
    --argjson notified "$([ "$notified" = 1 ] && echo true || echo false)" \
    --arg summary "$summary" \
    '{schema: 1, at: $at, epoch: $epoch, started_epoch: $started_epoch, slot: $slot, slot_epoch: $slot_epoch,
      rc: $rc, status: $status, backup_id: (if $dir == "" then null else ($dir | split("/") | last) end),
      attempts: $attempts, next_retry_epoch: $next, next_retry_at: $next_at,
      notified: $notified, summary: $summary}' | atomic_write "$FAILURE_FILE"
}

# Should THIS container page about a failure? Only on the transition into
# failure (the run before it succeeded, i.e. the first failed attempt since
# the newest success) and only while the previous backup is still fresh.
# Repeats, reminders, staleness and "recovered" belong to ops-watch, which
# reads the records — one incident, one page (FIX131 D).
# failure_is_news <last_success_epoch> <now>
failure_is_news() {
  local last="$1" now="$2" f_epoch
  if [ "$last" -gt 0 ] && [ $((now - last)) -ge $((BACKUP_MAX_AGE_H * 3600)) ]; then return 1; fi
  if [ -s "$FAILURE_FILE" ]; then
    f_epoch=$(jq -r '.epoch // 0' "$FAILURE_FILE" 2>/dev/null || echo 0)
    [ "$f_epoch" -gt "$last" ] && return 1
  fi
  return 0
}

# offsite_failure_is_news — the previous success's off-site copy did not fail.
offsite_failure_is_news() {
  local prev
  prev=$(jq -r '.offsite.status // "none"' "$SUCCESS_FILE" 2>/dev/null || echo none)
  [ "$prev" != "fail" ]
}
