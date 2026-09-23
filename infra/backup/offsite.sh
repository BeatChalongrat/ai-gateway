#!/usr/bin/env bash
# FIX130 E1 — the off-site copy (docs/ops/backups.md, "Off-site copy").
#
# The local backup lives on the same machine and disk as the databases; a
# dead disk, a stolen or burnt PC, or ransomware takes both. This script puts
# every run into an S3-compatible bucket (Backblaze B2 / Cloudflare R2 / AWS
# S3 / a company S3) with restic: encrypted on this machine before upload
# (the bucket only ever holds ciphertext), deduplicated, versioned.
#
#   offsite.sh copy <backup dir>   restic backup of the run directory, the
#                                  off-site restore drill (restore that
#                                  snapshot to a temp dir, sha256 of the three
#                                  files vs manifest.json), retention
#                                  (forget --prune), and on the check day a
#                                  `restic check --read-data-subset`.
#                                  Writes <dir>/offsite.json. Exit 0 = copied
#                                  and proven; 3 = a step failed; 0 with
#                                  "not configured" when the repository is unset.
#   offsite.sh check               first-time setup: init the repository if it
#                                  does not exist, then a tiny round trip
#                                  (back up a random file, restore it, compare,
#                                  forget it) and `restic check`.
#
# Configuration (all optional; unset BACKUP_OFFSITE_REPOSITORY = off):
#   BACKUP_OFFSITE_REPOSITORY          e.g. s3:https://s3.us-west-004.backblazeb2.com/<bucket>/sran
#   BACKUP_OFFSITE_PASSWORD            the restic repository password (encryption key!)
#   BACKUP_OFFSITE_ACCESS_KEY_ID       S3 access key id      -> AWS_ACCESS_KEY_ID
#   BACKUP_OFFSITE_SECRET_ACCESS_KEY   S3 secret access key  -> AWS_SECRET_ACCESS_KEY
#   BACKUP_OFFSITE_REGION              optional              -> AWS_DEFAULT_REGION (R2: auto)
#   BACKUP_OFFSITE_KEEP_DAILY/_WEEKLY/_MONTHLY   retention, default 14 / 8 / 12
#   BACKUP_OFFSITE_CHECK_WEEKDAY       1-7 (date +%u), default 7 = Sunday; "always"/"never"
#   BACKUP_OFFSITE_CHECK_SUBSET        default 5%  (restic check --read-data-subset)
#   BACKUP_OFFSITE_HOST                restic --host of every snapshot, default "sran"
#   BACKUP_OFFSITE_TIMEOUT_S           ceiling per restic call, default 7200
#   BACKUP_OFFSITE_CONNECT_TIMEOUT_S   ceiling for the first contact, default 300
#
# Nothing secret is ever printed: restic's output passes through lib.sh
# `redact` (which replaces the literal password/keys too), and the repository
# is only ever named by its scheme and host.
set -uo pipefail
. /lib.sh

OFFSITE_TAG="sran-backup"
CHECK_TAG="sran-offsite-check"
HOST_TAG="${BACKUP_OFFSITE_HOST:-sran}"
KEEP_DAILY="${BACKUP_OFFSITE_KEEP_DAILY:-14}"
KEEP_WEEKLY="${BACKUP_OFFSITE_KEEP_WEEKLY:-8}"
KEEP_MONTHLY="${BACKUP_OFFSITE_KEEP_MONTHLY:-12}"
CHECK_WEEKDAY="${BACKUP_OFFSITE_CHECK_WEEKDAY:-7}"
CHECK_SUBSET="${BACKUP_OFFSITE_CHECK_SUBSET:-5%}"
STEP_TIMEOUT="${BACKUP_OFFSITE_TIMEOUT_S:-7200}"
CONNECT_TIMEOUT="${BACKUP_OFFSITE_CONNECT_TIMEOUT_S:-300}"

# The repository as it may be printed: scheme + host, never path/credentials.
repo_label() {
  local r="${BACKUP_OFFSITE_REPOSITORY:-}" scheme rest host
  scheme="${r%%:*}"
  rest="${r#*:}"
  rest="${rest#http://}"; rest="${rest#https://}"
  rest="${rest##*@}"
  host="${rest%%/*}"
  printf '%s %s' "$scheme" "$host"
}

offsite_configured() { [ -n "${BACKUP_OFFSITE_REPOSITORY:-}" ]; }

setup_env() {
  export RESTIC_REPOSITORY="$BACKUP_OFFSITE_REPOSITORY"
  # The endpoint form restic prints in network errors (for lib.sh redact).
  export BACKUP_OFFSITE_REPOSITORY_URL="${BACKUP_OFFSITE_REPOSITORY#*:}"
  export RESTIC_PASSWORD="${BACKUP_OFFSITE_PASSWORD:-}"
  [ -n "${BACKUP_OFFSITE_ACCESS_KEY_ID:-}" ] && export AWS_ACCESS_KEY_ID="$BACKUP_OFFSITE_ACCESS_KEY_ID"
  [ -n "${BACKUP_OFFSITE_SECRET_ACCESS_KEY:-}" ] && export AWS_SECRET_ACCESS_KEY="$BACKUP_OFFSITE_SECRET_ACCESS_KEY"
  [ -n "${BACKUP_OFFSITE_REGION:-}" ] && export AWS_DEFAULT_REGION="$BACKUP_OFFSITE_REGION"
  # The cache survives `run --rm` (it lives next to the backups) and is not
  # a backup directory, so backup.sh's prune never touches it.
  export RESTIC_CACHE_DIR="${BACKUP_OFFSITE_CACHE_DIR:-$BACKUP_ROOT/.restic-cache}"
  mkdir -p "$RESTIC_CACHE_DIR" 2>/dev/null || true
  export RESTIC_PROGRESS_FPS=0.0166  # one progress line a minute at most
}

WORK=""
cleanup() { [ -n "$WORK" ] && rm -rf "$WORK"; }
trap cleanup EXIT

# rs <label> <timeout_s> <restic args...>: run restic; stdout kept in
# $WORK/<label>.out (JSON when asked), stderr logged (redacted, last 20 lines).
rs() {
  local label="$1" secs="$2"; shift 2
  local rc=0
  timeout --kill-after=30 "$secs" restic --retry-lock 2m "$@" >"$WORK/$label.out" 2>"$WORK/$label.err" || rc=$?
  if [ -s "$WORK/$label.err" ]; then
    redact < "$WORK/$label.err" | grep -v '^$' | tail -n 20 | while IFS= read -r line; do
      log "offsite: restic $label: $line"
    done
  fi
  [ $rc -eq 124 ] && log "offsite: restic $label: timed out after ${secs}s"
  return $rc
}

# Why the first contact failed, in words (restic >= 0.17 exit codes).
explain_rc() {
  case "$1" in
    10) echo "the repository does not exist" ;;
    11) echo "the repository is locked by another process" ;;
    12) echo "wrong repository password" ;;
    124|137) echo "the bucket did not answer in time (unreachable?)" ;;
    *) echo "restic exit $1 (unreachable bucket, bad keys or no permission)" ;;
  esac
}

# open_repo [init]: make sure the repository answers; with "init", create
# it when it does not exist. Sets OPEN_ERR on failure.
OPEN_ERR=""
open_repo() {
  local rc=0
  if [ -z "${BACKUP_OFFSITE_PASSWORD:-}" ]; then
    OPEN_ERR="BACKUP_OFFSITE_PASSWORD is not set"; return 1
  fi
  rs probe "$CONNECT_TIMEOUT" cat config --quiet || rc=$?
  if [ $rc -eq 0 ]; then return 0; fi
  if [ $rc -eq 10 ] && [ "${1:-}" = "init" ]; then
    log "offsite: repository does not exist yet at $(repo_label) — restic init"
    if rs init "$CONNECT_TIMEOUT" init --quiet; then
      log "offsite: repository initialised (keep BACKUP_OFFSITE_PASSWORD off this machine: without it the copy cannot be read)"
      return 0
    fi
    OPEN_ERR="restic init failed"; return 1
  fi
  OPEN_ERR="$(explain_rc $rc)"; return 1
}

# ---------------------------------------------------------------------------
# copy <dir>
# ---------------------------------------------------------------------------
copy_run() {
  local dir="${1:?usage: offsite.sh copy <backup dir>}"
  local manifest="$dir/manifest.json" out="$dir/offsite.json" t0
  t0=$(date +%s)
  [ -f "$manifest" ] || die "offsite: no manifest at $manifest"
  WORK=$(mktemp -d)
  : > "$WORK/checks.jsonl"

  if ! offsite_configured; then
    log "offsite copy: not configured (BACKUP_OFFSITE_REPOSITORY is empty) — local backup only"
    jq -n --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" '{schema: 1, at: $at, configured: false}' > "$out"
    return 0
  fi
  setup_env
  log "offsite: copy start dir=$dir repository=$(repo_label) host=$HOST_TAG"

  local failed_step="" snap="" short="" upload='{}' retention='{}' check_ran=false check_pass=null
  check() {  # check <name> <pass> <expected> <actual>
    jq -n -c --arg name "$1" --argjson pass "$2" --arg expected "$3" --arg actual "$4" \
      '{name: $name, pass: $pass, expected: $expected, actual: $actual}' >> "$WORK/checks.jsonl"
    if [ "$2" = true ]; then log "offsite: PASS $1"; else log "offsite: FAIL $1 expected=$3 actual=$4"; fi
  }

  # 1. Reach the repository (create it on first use).
  if ! open_repo init; then
    failed_step="connect"; check "connect" false "repository answers" "$OPEN_ERR"
  fi

  # 2. Upload the run directory.
  if [ -z "$failed_step" ]; then
    rs unlock 120 unlock --quiet || true   # removes STALE locks only
    local bt0; bt0=$(date +%s)
    if rs backup "$STEP_TIMEOUT" backup --json --host "$HOST_TAG" --tag "$OFFSITE_TAG" \
          --tag "id=$(basename "$dir")" "$dir"; then
      upload=$(grep '"message_type":"summary"' "$WORK/backup.out" | tail -n 1)
      snap=$(printf '%s' "$upload" | jq -r '.snapshot_id // empty')
    fi
    if [ -n "$snap" ]; then
      short="${snap:0:8}"
      upload=$(printf '%s' "$upload" | jq -c --argjson d "$(( $(date +%s) - bt0 ))" \
        '{files_new, files_changed, files_unmodified, data_added, data_added_packed,
          total_bytes_processed, duration_s: $d}')
      log "offsite: snapshot $short uploaded: $(printf '%s' "$upload" | jq -r \
        '"\(.files_new) new / \(.files_changed) changed / \(.files_unmodified) unchanged files; \(.data_added) bytes new (\(.data_added_packed) packed) of \(.total_bytes_processed) processed in \(.duration_s)s"')"
      check "upload" true "snapshot" "$short"
    else
      upload='{}'
      failed_step="upload"; check "upload" false "snapshot" "restic backup failed"
    fi
  fi

  # 3. The off-site restore drill: that snapshot, restored, sha256 vs manifest.
  if [ -z "$failed_step" ]; then
    local target="$WORK/restore" rdir f want got
    mkdir -p "$target"
    if rs restore "$STEP_TIMEOUT" restore "$snap" --target "$target" --quiet; then
      rdir="$target$dir"
      for f in $(jq -r '.files | keys[]' "$manifest") manifest.json; do
        if [ "$f" = manifest.json ]; then want=$(file_sha256 "$manifest")
        else want=$(jq -r --arg f "$f" '.files[$f].sha256' "$manifest"); fi
        if [ -f "$rdir/$f" ]; then got=$(file_sha256 "$rdir/$f"); else got="missing"; fi
        if [ "$got" = "$want" ]; then check "restore-sha256:$f" true "$want" "$got"
        else check "restore-sha256:$f" false "$want" "$got"; failed_step="restore"; fi
      done
    else
      failed_step="restore"; check "restore" false "restic restore rc=0" "failed"
    fi
    rm -rf "$target"
  fi

  # 4. Retention — only once the new copy is proven.
  if [ -z "$failed_step" ]; then
    if rs forget "$STEP_TIMEOUT" forget --json --host "$HOST_TAG" --tag "$OFFSITE_TAG" --group-by host \
          --keep-daily "$KEEP_DAILY" --keep-weekly "$KEEP_WEEKLY" --keep-monthly "$KEEP_MONTHLY" --prune; then
      retention=$(head -n 1 "$WORK/forget.out" | jq -c --arg d "$KEEP_DAILY" --arg w "$KEEP_WEEKLY" --arg m "$KEEP_MONTHLY" \
        '{keep_daily: ($d|tonumber), keep_weekly: ($w|tonumber), keep_monthly: ($m|tonumber),
          kept: ([.[]?.keep // [] | length] | add // 0), removed: ([.[]?.remove // [] | length] | add // 0)}' 2>/dev/null \
        || echo '{}')
      log "offsite: retention kept=$(printf '%s' "$retention" | jq -r '.kept // "?"') removed=$(printf '%s' "$retention" | jq -r '.removed // "?"') (daily $KEEP_DAILY, weekly $KEEP_WEEKLY, monthly $KEEP_MONTHLY)"
      check "retention" true "forget --prune rc=0" "rc=0"
    else
      failed_step="retention"; check "retention" false "forget --prune rc=0" "failed"
    fi
  fi

  # 5. Weekly: read back a random subset of the packs (catches bit rot in
  #    the bucket that the drill of today's new data would not).
  if [ -z "$failed_step" ]; then
    local today; today=$(date +%u)
    if [ "$CHECK_WEEKDAY" = "always" ] || [ "$CHECK_WEEKDAY" = "$today" ]; then
      check_ran=true
      if rs check "$STEP_TIMEOUT" check --read-data-subset="$CHECK_SUBSET" --quiet; then
        check_pass=true; check "check:read-data-subset=$CHECK_SUBSET" true "restic check rc=0" "rc=0"
      else
        check_pass=false; failed_step="check"; check "check:read-data-subset=$CHECK_SUBSET" false "restic check rc=0" "failed"
      fi
    fi
  fi

  local pass=true
  [ -n "$failed_step" ] && pass=false
  jq -n \
    --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --arg backup "$dir" --arg repository "$(repo_label)" \
    --arg host "$HOST_TAG" --arg snapshot "$snap" --arg failed_step "$failed_step" \
    --argjson pass "$pass" --argjson upload "$upload" --argjson retention "$retention" \
    --argjson check_ran "$check_ran" --argjson check_pass "$check_pass" --arg subset "$CHECK_SUBSET" \
    --argjson duration_s "$(( $(date +%s) - t0 ))" \
    --argjson checks "$(jq -s . "$WORK/checks.jsonl")" \
    '{schema: 1, at: $at, configured: true, backup: $backup, repository: $repository, host: $host,
      pass: $pass, failed_step: (if $failed_step == "" then null else $failed_step end),
      snapshot: (if $snapshot == "" then null else $snapshot end),
      upload: $upload, retention: $retention,
      check: {ran: $check_ran, pass: $check_pass, read_data_subset: $subset},
      duration_s: $duration_s, checks: $checks}' > "$out"

  if [ "$pass" = true ]; then
    log "offsite: copy PASS snapshot=$short duration_s=$(( $(date +%s) - t0 )) -> $out"
    return 0
  fi
  log "offsite: copy FAIL at step '$failed_step' -> $out"
  return 3
}

# ---------------------------------------------------------------------------
# check — first-time setup / "is it still working?"
# ---------------------------------------------------------------------------
check_repo() {
  if ! offsite_configured; then
    log "offsite copy: not configured (set BACKUP_OFFSITE_REPOSITORY, BACKUP_OFFSITE_PASSWORD, BACKUP_OFFSITE_ACCESS_KEY_ID, BACKUP_OFFSITE_SECRET_ACCESS_KEY)"
    return 2
  fi
  setup_env
  WORK=$(mktemp -d)
  log "offsite: check start repository=$(repo_label) host=$HOST_TAG"
  if ! open_repo init; then
    log "offsite: check FAIL — $OPEN_ERR"
    return 3
  fi
  local src="$WORK/offsite-check" dst="$WORK/restored" snap want got rc=0
  mkdir -p "$src" "$dst"
  head -c 65536 /dev/urandom > "$src/random.bin"
  date -u +%Y-%m-%dT%H:%M:%SZ > "$src/written_at.txt"
  want=$(cat "$src"/* | sha256sum | cut -d' ' -f1)
  if ! rs backup 600 backup --json --host "$HOST_TAG" --tag "$CHECK_TAG" "$src"; then
    log "offsite: check FAIL — upload of a 64 KiB test file failed (write permission on the bucket?)"
    return 3
  fi
  snap=$(grep '"message_type":"summary"' "$WORK/backup.out" | tail -n 1 | jq -r '.snapshot_id // empty')
  if ! rs restore 600 restore "$snap" --target "$dst" --quiet; then
    log "offsite: check FAIL — restore of the test snapshot failed (read permission on the bucket?)"
    return 3
  fi
  got=$(cat "$dst$src"/* | sha256sum | cut -d' ' -f1)
  if [ "$got" != "$want" ]; then
    log "offsite: check FAIL — restored test file differs"
    rc=3
  else
    log "offsite: round trip PASS (64 KiB up, down, sha256 equal, snapshot ${snap:0:8})"
  fi
  if rs forget-check 600 forget "$snap" --quiet; then
    log "offsite: test snapshot removed"
  else
    log "offsite: WARN could not remove the test snapshot — the key may lack delete permission; then retention (forget --prune) will fail too (docs/ops/backups.md)"
  fi
  if rs check 1800 check --quiet; then log "offsite: restic check PASS (repository structure)"; else log "offsite: restic check FAIL"; rc=3; fi
  if rs snapshots 300 snapshots --json --host "$HOST_TAG" --tag "$OFFSITE_TAG"; then
    log "offsite: $(jq 'length' "$WORK/snapshots.out") backup snapshot(s) in the repository; newest: $(jq -r 'max_by(.time) | .time // "none"' "$WORK/snapshots.out" 2>/dev/null)"
  fi
  [ $rc -eq 0 ] && log "offsite: check PASS"
  return $rc
}

case "${1:-}" in
  copy)  shift; copy_run "$@" ;;
  check) check_repo ;;
  *) echo "usage: offsite.sh copy <backup dir> | offsite.sh check" >&2; exit 2 ;;
esac
