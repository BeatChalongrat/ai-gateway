#!/usr/bin/env bash
# FIX129 A5 — the backup container's entrypoint (docs/ops/backups.md).
#
#   (no argument)   the scheduler: sleep until the next BACKUP_AT (HH:MM in
#                   $TZ, default 03:15 Asia/Bangkok), run backup + drill,
#                   notify, repeat.
#   now             one backup + drill + off-site copy + notification, then
#                   exit (0 = the backup was written, the drill passed and the
#                   off-site copy was proven or is not configured; 1 backup
#                   failed; 2 drill failed; 3 off-site copy failed).
#   backup          one backup only (no drill).
#   drill <dir>     the restore drill on an existing backup directory.
#   offsite <dir>   the off-site copy of an existing backup directory.
#   offsite-check   first-time setup of the off-site repository: init it if
#                   needed + a tiny upload/restore round trip (offsite.sh).
#   notify <level> <title> <message>   send a test alert.
#
# FIX130 E1 — the off-site copy (offsite.sh, restic to an S3-compatible
# bucket) runs after every backup whose drill passed; unset
# BACKUP_OFFSITE_REPOSITORY = "offsite copy: not configured", never a failure.
#
# Notifications: a failure is always sent (priority high, tag
# rotating_light); a success is sent only when BACKUP_NOTIFY_SUCCESS=1.
set -uo pipefail
. /lib.sh

export TZ="${TZ:-Asia/Bangkok}"
BACKUP_AT="${BACKUP_AT:-03:15}"
NOTIFY_SUCCESS="${BACKUP_NOTIFY_SUCCESS:-0}"

case "$BACKUP_AT" in
  [0-2][0-9]:[0-5][0-9]) ;;
  *) die "BACKUP_AT must be HH:MM (got '$BACKUP_AT')" ;;
esac

SUMMARY=""
# run_once: backup + drill + off-site copy. Sets SUMMARY (one line, no
# secrets). Returns 0 ok, 1 backup failed, 2 drill failed, 3 off-site failed.
run_once() {
  local t0 rc=0 dir="" result=/tmp/backup.result
  t0=$(date +%s)
  rm -f "$result"
  if BACKUP_RESULT_FILE="$result" /backup.sh && [ -s "$result" ]; then
    dir=$(cat "$result")
  else
    rc=1
  fi
  local drill="not run"
  if [ $rc -eq 0 ]; then
    if /drill.sh "$dir"; then drill="pass"; else rc=2; drill="FAIL"; fi
    if [ -f "$dir/drill.json" ]; then
      drill="$drill $(jq -r '"\(.checks_total - .checks_failed)/\(.checks_total) checks"' "$dir/drill.json")"
      if [ $rc -eq 2 ]; then
        drill="$drill: $(jq -r '[.checks[] | select(.pass == false) | .name] | join(", ")' "$dir/drill.json")"
      fi
    fi
  fi
  local offsite="not run"
  if [ $rc -eq 0 ]; then
    if /offsite.sh copy "$dir"; then offsite="pass"; else rc=3; offsite="FAIL"; fi
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
  return $rc
}

notify_result() {
  local rc="$1" title="SRAN backup FAILED"
  [ "$rc" -eq 3 ] && title="SRAN off-site backup FAILED"
  if [ "$rc" -ne 0 ]; then
    /notify.sh high "$title" "$SUMMARY" || log "notify: WARN alert not delivered"
  elif [ "$NOTIFY_SUCCESS" = "1" ]; then
    /notify.sh ok "SRAN backup OK" "$SUMMARY" || log "notify: WARN alert not delivered"
  fi
}

case "${1:-loop}" in
  now)
    run_once; rc=$?
    log "summary: $SUMMARY"
    notify_result $rc
    exit $rc
    ;;
  backup) exec /backup.sh ;;
  drill)  shift; exec /drill.sh "$@" ;;
  offsite) shift; exec /offsite.sh copy "$@" ;;
  offsite-check) exec /offsite.sh check ;;
  notify) shift; exec /notify.sh "$@" ;;
  loop)
    trap 'log "scheduler stopped"; exit 0' TERM INT
    log "scheduler start BACKUP_AT=$BACKUP_AT TZ=$TZ keep_days=${BACKUP_KEEP_DAYS:-14} root=$BACKUP_ROOT uploads=$UPLOADS_DIR sources: $(describe_sources) offsite: $([ -n "${BACKUP_OFFSITE_REPOSITORY:-}" ] && echo configured || echo 'not configured')"
    [ -d "$BACKUP_ROOT" ] || die "backup root $BACKUP_ROOT is not mounted"
    while :; do
      now=$(date +%s)
      next=$(date -d "today $BACKUP_AT" +%s)
      [ "$next" -le "$now" ] && next=$(date -d "tomorrow $BACKUP_AT" +%s)
      log "next backup at $(date -d "@$next" +%Y-%m-%dT%H:%M:%S%z) (in $((next - now)) s)"
      sleep $((next - now)) & wait $! || true
      run_once; rc=$?
      log "summary: $SUMMARY"
      notify_result $rc
      # Never run twice in the same minute if the run was instant.
      sleep 61 & wait $! || true
    done
    ;;
  *)
    echo "usage: entrypoint.sh [now|backup|drill <dir>|offsite <dir>|offsite-check|notify <level> <title> <message>]" >&2
    exit 2
    ;;
esac
