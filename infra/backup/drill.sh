#!/usr/bin/env bash
# FIX129 A5 — the restore drill: prove a backup directory can be restored.
#
#   drill.sh /backups/<YYYYMMDD_HHMMSS>
#
# For each dump: create a scratch database on the SAME server the dump came
# from (createdb --template=template0 <name>_drill), pg_restore --no-owner
# --no-privileges into it, count the same key tables and compare with the
# manifest (exact: manifest counts and dump share one snapshot). The uploads
# tar is listed and its entry count compared. Every file's sha256 is
# re-checked first. Writes <dir>/drill.json with pass/fail per check.
# The scratch databases are ALWAYS dropped (EXIT trap). Exit 1 on any
# failed check.
set -euo pipefail
. /lib.sh

dir="${1:?usage: drill.sh <backup dir>}"
manifest="$dir/manifest.json"
[ -f "$manifest" ] || die "no manifest at $manifest"
t0=$(date +%s)
work=$(mktemp -d)
: > "$work/checks.jsonl"

# check <name> <pass:true|false> <expected> <actual> [detail]
check() {
  jq -n -c --arg name "$1" --argjson pass "$2" --arg expected "$3" --arg actual "$4" --arg detail "${5:-}" \
     '{name: $name, pass: $pass, expected: $expected, actual: $actual} + (if $detail == "" then {} else {detail: $detail} end)' \
     >> "$work/checks.jsonl"
  if [ "$2" = true ]; then log "drill: PASS $1 ($4)"; else log "drill: FAIL $1 expected=$3 actual=$4 ${5:-}"; fi
}

# admin <gw|chat> <maintenance db> ... — createdb/dropdb need a DB to talk to.
drop_scratch() {
  PGPASSWORD="$GW_PASSWORD"   dropdb --if-exists --force -h "$GW_HOST"   -p "$GW_PORT"   -U "$GW_USER"   --maintenance-db="$GW_DB"   "$GW_DRILL_DB"   >/dev/null 2>&1 || true
  PGPASSWORD="$CHAT_PASSWORD" dropdb --if-exists --force -h "$CHAT_HOST" -p "$CHAT_PORT" -U "$CHAT_USER" --maintenance-db="$CHAT_DB" "$CHAT_DRILL_DB" >/dev/null 2>&1 || true
}
on_exit() {
  drop_scratch
  log "drill: scratch databases dropped ($GW_DRILL_DB, $CHAT_DRILL_DB)"
  rm -rf "$work"
}
trap on_exit EXIT

log "drill start dir=$dir"

# 1. Integrity of every file named in the manifest.
for f in $(jq -r '.files | keys[]' "$manifest"); do
  want=$(jq -r --arg f "$f" '.files[$f].sha256' "$manifest")
  if [ -f "$dir/$f" ]; then
    got=$(file_sha256 "$dir/$f")
    if [ "$got" = "$want" ]; then check "sha256:$f" true "$want" "$got"; else check "sha256:$f" false "$want" "$got"; fi
  else
    check "sha256:$f" false "$want" "missing"
  fi
done

# 2. Restore each dump into a scratch DB and compare the key-table counts.
restore_and_count() {
  local which="$1" file="$2" scratch="$3" host port user password maint
  case "$which" in
    gw)   host=$GW_HOST;   port=$GW_PORT;   user=$GW_USER;   password=$GW_PASSWORD;   maint=$GW_DB ;;
    chat) host=$CHAT_HOST; port=$CHAT_PORT; user=$CHAT_USER; password=$CHAT_PASSWORD; maint=$CHAT_DB ;;
  esac
  local rc=0 err="$work/$which.restore.err"
  PGPASSWORD="$password" dropdb --if-exists --force -h "$host" -p "$port" -U "$user" --maintenance-db="$maint" "$scratch" >/dev/null 2>&1 || true
  if ! PGPASSWORD="$password" createdb --template=template0 -h "$host" -p "$port" -U "$user" --maintenance-db="$maint" "$scratch" 2>"$err"; then
    check "restore:$which" false "createdb $scratch ok" "createdb failed" "$(head -c 300 "$err" | tr '\n' ' ')"
    return 0
  fi
  log "$which: created scratch db $scratch on $host, restoring $(basename "$file")"
  PGPASSWORD="$password" pg_restore --no-owner --no-privileges -h "$host" -p "$port" -U "$user" \
      -d "$scratch" "$dir/$file" 2>"$err" || rc=$?
  if [ $rc -eq 0 ]; then
    check "restore:$which" true "pg_restore rc=0" "rc=0"
  else
    check "restore:$which" false "pg_restore rc=0" "rc=$rc" "$(grep -m1 -i 'error' "$err" | head -c 300)"
  fi
  local t want got
  for t in $(jq -r --arg w "$which" '.[if $w == "gw" then "gateway" else "chat" end].counts | keys[]' "$manifest"); do
    want=$(jq -r --arg w "$which" --arg t "$t" '.[if $w == "gw" then "gateway" else "chat" end].counts[$t]' "$manifest")
    got=$(count_rows "$which" "$scratch" "$t" 2>"$err" || echo "error")
    if [ "$got" = "$want" ]; then check "rows:$which:$t" true "$want" "$got"
    else check "rows:$which:$t" false "$want" "$got" "$(head -c 200 "$err" | tr '\n' ' ')"; fi
  done
}
restore_and_count gw   "$(jq -r .gateway.file "$manifest")" "$GW_DRILL_DB"
restore_and_count chat "$(jq -r .chat.file    "$manifest")" "$CHAT_DRILL_DB"

# 3. The uploads tar lists cleanly and holds what the manifest says.
tarfile="$dir/$(jq -r .uploads.file "$manifest")"
want=$(jq -r .uploads.entries "$manifest")
if got=$(tar -tzf "$tarfile" 2>"$work/tar.err" | wc -l | tr -d ' '); then
  if [ "$got" = "$want" ]; then check "uploads:entries" true "$want" "$got"
  else check "uploads:entries" false "$want" "$got" "$(head -c 200 "$work/tar.err" | tr '\n' ' ')"; fi
else
  check "uploads:entries" false "$want" "tar -t failed" "$(head -c 200 "$work/tar.err" | tr '\n' ' ')"
fi

# 4. drill.json
total=$(wc -l < "$work/checks.jsonl" | tr -d ' ')
failed=$(jq -s '[.[] | select(.pass == false)] | length' "$work/checks.jsonl")
jq -s -n \
  --arg dir "$dir" --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --argjson duration_s "$(( $(date +%s) - t0 ))" \
  --argjson checks "$(jq -s . "$work/checks.jsonl")" \
  --argjson total "$total" --argjson failed "$failed" \
  --arg gw_scratch "$GW_DRILL_DB" --arg chat_scratch "$CHAT_DRILL_DB" \
  '{schema: 1, backup: $dir, at: $at, duration_s: $duration_s,
    pass: ($failed == 0), checks_total: $total, checks_failed: $failed,
    scratch: {gateway: $gw_scratch, chat: $chat_scratch}, checks: $checks}' > "$dir/drill.json"

if [ "$failed" -eq 0 ]; then
  log "drill PASS $total/$total checks duration_s=$(( $(date +%s) - t0 )) -> $dir/drill.json"
  exit 0
fi
log "drill FAIL $failed/$total checks failed -> $dir/drill.json"
exit 1
