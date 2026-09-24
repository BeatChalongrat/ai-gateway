#!/usr/bin/env bash
# FIX129 A5 — the backup container's entrypoint (docs/ops/backups.md).
#
#   (no argument)   the scheduler (FIX131): every BACKUP_TICK_S (60 s) compare
#                   the WALL clock with BACKUP_AT (HH:MM in $TZ, default 03:15
#                   Asia/Bangkok) and run backup + drill + off-site copy when
#                   the newest successful backup is older than the latest
#                   slot that has passed — so a window missed while the PC
#                   slept or the container was down runs as soon as it is
#                   back (schedule.sh). Failures retry with backoff.
#   tick            one scheduler decision (and the run, if due), then exit.
#   now             one backup + drill + off-site copy + notification, then
#                   exit (0 = the backup was written, the drill passed and the
#                   off-site copy was proven or is not configured; 1 backup
#                   failed; 2 drill failed; 3 off-site copy failed; 4 another
#                   backup is running).
#   backup          one backup only (no drill).
#   drill <dir>     the restore drill on an existing backup directory.
#   offsite <dir>   the off-site copy of an existing backup directory.
#   offsite-check   first-time setup of the off-site repository: init it if
#                   needed + a tiny upload/restore round trip (offsite.sh).
#   notify <level> <title> <message>   send a TEST alert (FIX131: always
#                   "[TEST]", only to ALERT_TEST_NTFY_URL — never the owner's
#                   topic).
#
# FIX130 E1 — the off-site copy (offsite.sh, restic to an S3-compatible
# bucket) runs after every backup whose drill passed; unset
# BACKUP_OFFSITE_REPOSITORY = "offsite copy: not configured", never a failure.
#
# Notifications (FIX131): this container pages on the TRANSITION into failure
# (the first failed attempt since the newest success, while that success is
# still fresh), priority high; a success is sent only when
# BACKUP_NOTIFY_SUCCESS=1. Everything after that — retries that fail again,
# "no fresh backup", reminders, "recovered" — is ops-watch's, which reads
# last_success.json / last_failure.json, so one incident is one page.
set -uo pipefail
. /lib.sh
. /schedule.sh

# Where backup.sh / drill.sh / offsite.sh / notify.sh live ("" = /). The tests
# point it at stubs; nothing else sets it.
S="${BACKUP_SCRIPTS_DIR:-}"

export TZ="${TZ:-Asia/Bangkok}"
BACKUP_AT="${BACKUP_AT:-03:15}"
NOTIFY_SUCCESS="${BACKUP_NOTIFY_SUCCESS:-0}"

case "$BACKUP_AT" in
  [0-2][0-9]:[0-5][0-9]) ;;
  *) die "BACKUP_AT must be HH:MM (got '$BACKUP_AT')" ;;
esac

SUMMARY=""
RUN_DIR=""
# run_once: backup + drill + off-site copy. Sets SUMMARY (one line, no
# secrets) and RUN_DIR. Returns 0 ok, 1 backup failed, 2 drill failed, 3
# off-site failed.
run_once() {
  local t0 rc=0 dir="" result=/tmp/backup.result
  t0=$(date +%s)
  rm -f "$result"
  if BACKUP_RESULT_FILE="$result" "$S/backup.sh" && [ -s "$result" ]; then
    dir=$(cat "$result")
  else
    rc=1
  fi
  local drill="not run"
  if [ $rc -eq 0 ]; then
    if "$S/drill.sh" "$dir"; then drill="pass"; else rc=2; drill="FAIL"; fi
    if [ -f "$dir/drill.json" ]; then
      drill="$drill $(jq -r '"\(.checks_total - .checks_failed)/\(.checks_total) checks"' "$dir/drill.json")"
      if [ $rc -eq 2 ]; then
        drill="$drill: $(jq -r '[.checks[] | select(.pass == false) | .name] | join(", ")' "$dir/drill.json")"
      fi
    fi
  fi
  local offsite="not run"
  if [ $rc -eq 0 ]; then
    if "$S/offsite.sh" copy "$dir"; then offsite="pass"; else rc=3; offsite="FAIL"; fi
    if [ -f "$dir/offsite.json" ]; then
      offsite=$(jq -r --arg v "$offsite" '
        def human: if . == null then "?" elif . >= 1048576 then "\(. / 1048576 * 10 | round / 10) MB" else "\(. / 1024 | round) KB" end;
        if .configured == false then "not configured"
        elif .pass then "\($v) snapshot \(.snapshot[0:8]), \(.upload.data_added | human) new of \(.upload.total_bytes_processed | human), restore \([.checks[] | select(.name | startswith("restore-sha256:")) | select(.pass)] | length)/\([.checks[] | select(.name | startswith("restore-sha256:"))] | length) sha256\(if .check.ran then ", check \(.check.read_data_subset) pass" else "" end)"
        elif .failed_step == "connect" then "\($v) at connect (\([.checks[] | select(.name == "connect") | .actual] | first))"
        else "\($v) at \(.failed_step): \([.checks[] | select(.pass == false) | .name] | join(", "))" end
      ' "$dir/offsite.json")
    fi
  fi
  local dt=$(( $(date +%s) - t0 )) sizes="n/a"
  if [ -n "$dir" ] && [ -f "$dir/manifest.json" ]; then
    sizes=$(jq -r '
      def human: if . >= 1048576 then "\(. / 1048576 * 10 | round / 10) MB" else "\(. / 1024 | round) KB" end;
      "gateway.dump \(.files["gateway.dump"].bytes | human), sran_ai.dump \(.files["sran_ai.dump"].bytes | human), uploads.tar.gz \(.files["uploads.tar.gz"].bytes | human) (\(.uploads.entries) entries)"
    ' "$dir/manifest.json")
  fi
  local status="OK"
  [ $rc -eq 1 ] && status="BACKUP FAILED"
  [ $rc -eq 2 ] && status="DRILL FAILED"
  [ $rc -eq 3 ] && status="OFFSITE FAILED"
  SUMMARY="$status backup=${dir:-none} sizes: $sizes; duration ${dt}s; drill: $drill; offsite: $offsite"
  RUN_DIR="$dir"
  return $rc
}

# The page, in words for a person: what is wrong, what still works, what to
# do. The one-line summary follows as "Details".
# page_failure <rc> <last_success_epoch> <attempt>
page_failure() {
  local rc="$1" last="$2" attempt="$3" title body prev="none"
  [ "$last" -gt 0 ] && prev="$(date -d "@$last" '+%Y-%m-%d %H:%M %Z') ($(fmt_age $(( $(now_epoch) - last ))) old)"
  local retry="The next automatic try is in $(fmt_age "$(retry_backoff "$attempt")")."
  case "$rc" in
    1) title="Backup failed: no new backup was written"
       body="Tonight's backup of the databases and uploads did not complete. The previous backup is still there: $prev. $retry Look: docker logs ai-gateway-backup (the step that failed). Run one by hand: docker compose -p ai-gateway -f docker-compose.webchat.yml run --rm backup now" ;;
    2) title="Backup failed its restore test"
       body="Tonight's backup was written but could not be restored and counted in the drill, so it cannot be trusted. The newest proven backup is $prev. $retry Look: drill.json in the backup folder and docker logs ai-gateway-backup." ;;
    3) title="Off-site backup copy failed (the local backup is fine)"
       body="Tonight's local backup was written and proven, but the copy to the off-site bucket failed, so a dead PC would lose it. Look: offsite.json in the backup folder and docker logs ai-gateway-backup; check the bucket keys (docs/ops/backups.md, Off-site copy)." ;;
  esac
  "$S/notify.sh" high "$title" "$body
Details: $SUMMARY"
}

# record_outcome <rc> <started_epoch> <trigger> <attempt> <last_success_epoch>
# — writes last_success.json / last_failure.json and pages when this run is
# news (failure_is_news / offsite_failure_is_news). Sets NOTIFIED.
NOTIFIED=0
record_outcome() {
  local rc="$1" started="$2" trigger="$3" attempt="$4" last="$5" now off_status status
  now=$(now_epoch)
  NOTIFIED=0
  case "$rc" in
    0|3)
      off_status=$(jq -r 'if .configured == false then "not_configured" elif .pass == true then "pass" else "fail" end' \
                   "$RUN_DIR/offsite.json" 2>/dev/null || echo "not_run")
      [ "$rc" -eq 3 ] && off_status=fail
      if [ "$rc" -eq 3 ]; then
        if offsite_failure_is_news; then
          if page_failure 3 "$now" "$attempt"; then NOTIFIED=1; else log "notify: WARN alert not delivered"; fi
        else
          log "notify: off-site copy still failing — not paged again (ops-watch owns the open incident)"
        fi
      fi
      write_success_record "$RUN_DIR" "$started" "$trigger" "$attempt" "$off_status" "$NOTIFIED" \
        && log "record: $SUCCESS_FILE written (backup $(basename "$RUN_DIR"), off-site $off_status)"
      if [ "$rc" -eq 0 ] && [ "$NOTIFY_SUCCESS" = "1" ]; then
        "$S/notify.sh" ok "SRAN backup OK" "$SUMMARY" || log "notify: WARN alert not delivered"
      fi
      ;;
    *)
      status="BACKUP FAILED"
      [ "$rc" -eq 2 ] && status="DRILL FAILED"
      if failure_is_news "$last" "$now"; then
        if page_failure "$rc" "$last" "$attempt"; then NOTIFIED=1; else log "notify: WARN alert not delivered"; fi
      else
        log "notify: not paged by this container (attempt $attempt since the newest success, or that success is older than ${BACKUP_MAX_AGE_H} h) — ops-watch owns the open incident"
      fi
      write_failure_record "$rc" "$status" "$RUN_DIR" "$started" "$attempt" "$NOTIFIED" "$SUMMARY" \
        && log "record: $FAILURE_FILE written ($status, attempt $attempt, next try in $(fmt_age "$(retry_backoff "$attempt")"))"
      ;;
  esac
}

# One scheduler decision, logged on every change of decision and at least
# hourly while nothing changes (a heartbeat in `docker logs`).
LAST_DECISION_KEY=""
LAST_DECISION_LOG=0
RAN_OK_SLOT=""
scheduler_tick() {
  local now key rc started last
  now=$(now_epoch)
  decide "$now"
  if [ "$DECISION" = run ] && [ "$RAN_OK_SLOT" = "$SLOT" ]; then
    # This process already backed up this slot successfully but the record
    # did not land (disk full?) — never loop a backup every minute on that.
    DECISION=wait; REASON="slot $(fmt_time "$SLOT") already backed up by this process (its record could not be written — check the disk)"
  fi
  key="$DECISION|$SLOT|$NEXT_AT"
  if [ "$DECISION" != run ]; then
    if [ "$key" != "$LAST_DECISION_KEY" ] || [ $((now - LAST_DECISION_LOG)) -ge 3600 ]; then
      log "decision: $DECISION — $REASON"
      LAST_DECISION_KEY="$key"; LAST_DECISION_LOG="$now"
    fi
    return 0
  fi
  if ! lock_acquire; then
    log "decision: skip — another backup is running ($LOCK_HOLDER)"
    return 0
  fi
  log "decision: run ($TRIGGER, attempt $ATTEMPT) — $REASON"
  LAST_DECISION_KEY=""
  started="$now"; last="$LAST_OK"
  run_once; rc=$?
  log "summary: $SUMMARY"
  record_outcome $rc "$started" "$TRIGGER" "$ATTEMPT" "$last"
  if [ $rc -eq 0 ] || [ $rc -eq 3 ]; then RAN_OK_SLOT="$SLOT"; fi
  lock_release
  return 0
}

case "${1:-loop}" in
  now)
    [ -d "$BACKUP_ROOT" ] || die "backup root $BACKUP_ROOT is not mounted"
    if ! lock_acquire; then log "now: another backup is running ($LOCK_HOLDER) — not starting a second one"; exit 4; fi
    trap 'lock_release' EXIT
    decide "$(now_epoch)"
    started=$(now_epoch); last="$LAST_OK"
    attempt=1; [ "$TRIGGER" = retry ] && attempt="$ATTEMPT"
    run_once; rc=$?
    log "summary: $SUMMARY"
    record_outcome $rc "$started" manual "$attempt" "$last"
    exit $rc
    ;;
  tick)
    [ -d "$BACKUP_ROOT" ] || die "backup root $BACKUP_ROOT is not mounted"
    scheduler_tick
    exit 0
    ;;
  backup) exec "$S/backup.sh" ;;
  drill)  shift; exec "$S/drill.sh" "$@" ;;
  offsite) shift; exec "$S/offsite.sh" copy "$@" ;;
  offsite-check) exec "$S/offsite.sh" check ;;
  notify) shift; NOTIFY_TEST=1 exec "$S/notify.sh" "$@" ;;
  loop)
    trap 'lock_release; log "scheduler stopped"; exit 0' TERM INT
    log "scheduler start BACKUP_AT=$BACKUP_AT TZ=$TZ tick=${BACKUP_TICK_S}s retry=${BACKUP_RETRY_BASE_S}..${BACKUP_RETRY_MAX_S}s fresh<${BACKUP_MAX_AGE_H}h keep_days=${BACKUP_KEEP_DAYS:-14} root=$BACKUP_ROOT uploads=$UPLOADS_DIR sources: $(describe_sources) offsite: $([ -n "${BACKUP_OFFSITE_REPOSITORY:-}" ] && echo configured || echo 'not configured')"
    [ -d "$BACKUP_ROOT" ] || die "backup root $BACKUP_ROOT is not mounted"
    lock_reclaim_own
    while :; do
      scheduler_tick
      sleep "$BACKUP_TICK_S" & wait $! || true
    done
    ;;
  *)
    echo "usage: entrypoint.sh [now|tick|backup|drill <dir>|offsite <dir>|offsite-check|notify <level> <title> <message>]" >&2
    exit 2
    ;;
esac
