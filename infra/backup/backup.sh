#!/usr/bin/env bash
# FIX129 A5 — one backup run (docs/ops/backups.md).
#
#   /backups/<YYYYMMDD_HHMMSS>/
#     gateway.dump      pg_dump -Fc of the gateway DB   (postgres / ai_gateway)
#     sran_ai.dump      pg_dump -Fc of the chat DB      (postgres-webchat / sran_ai)
#     uploads.tar.gz    the chat_edge_uploads volume    (/uploads, read-only)
#     manifest.json     sizes, sha256, row counts of the key tables
#
# The row counts and the dump of each database come from ONE snapshot: a
# REPEATABLE READ transaction exports its snapshot (pg_export_snapshot), the
# counts run inside that transaction, and `pg_dump --snapshot` dumps the
# same snapshot. So the manifest says exactly what the dump holds even while
# the source keeps taking writes — and drill.sh can compare EXACTLY.
#
# Then directories older than BACKUP_KEEP_DAYS (default 14) are pruned.
# (The off-site copy of the run — FIX130 E1, offsite.sh — is entrypoint.sh's
# job after the drill; the manifest only records whether one is configured.)
# Any failure exits non-zero and the partial directory is renamed *.failed.
# If BACKUP_RESULT_FILE is set, the finished directory's path is written there.
set -euo pipefail
. /lib.sh

KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
RESULT_FILE="${BACKUP_RESULT_FILE:-}"

[ -d "$BACKUP_ROOT" ] || die "backup root $BACKUP_ROOT is not a directory (is it mounted?)"
[ -d "$UPLOADS_DIR" ] || die "uploads dir $UPLOADS_DIR is not a directory (is the volume mounted?)"

t0=$(date +%s)
id=$(date +%Y%m%d_%H%M%S)
dir="$BACKUP_ROOT/$id"
work=$(mktemp -d)
mkdir -p "$dir"

on_exit() {
  local rc=$?
  rm -rf "$work"
  if [ $rc -ne 0 ] && [ -d "$dir" ] && [ ! -f "$dir/manifest.json" ]; then
    mv "$dir" "$dir.failed" 2>/dev/null || true
    log "backup FAILED rc=$rc partial output kept at $dir.failed"
  fi
}
trap on_exit EXIT

log "backup start id=$id sources: $(describe_sources)"

# dump_db gw|chat <db> <outfile> <counts.json> <table>...
dump_db() {
  local which="$1" db="$2" out="$3" counts_json="$4"; shift 4
  local tables=("$@") host port user password
  case "$which" in
    gw)   host=$GW_HOST;   port=$GW_PORT;   user=$GW_USER;   password=$GW_PASSWORD ;;
    chat) host=$CHAT_HOST; port=$CHAT_PORT; user=$CHAT_USER; password=$CHAT_PASSWORD ;;
    *) die "dump_db: unknown source '$which'" ;;
  esac

  # The snapshot-holding session. Its stdin/stdout are the coprocess pipes;
  # it stays open until we COMMIT after the dump.
  coproc SNAP { PGPASSWORD="$password" psql -X -q -At -v ON_ERROR_STOP=1 \
                  -h "$host" -p "$port" -U "$user" -d "$db" 2>"$work/$which.psql.err"; }
  local snap_pid=$SNAP_PID
  echo "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;" >&"${SNAP[1]}"
  echo "SELECT pg_export_snapshot();" >&"${SNAP[1]}"
  local snap=""
  read -r snap <&"${SNAP[0]}" || true
  if [ -z "$snap" ]; then
    wait "$snap_pid" || true
    die "$which: could not open a snapshot on $host/$db: $(head -c 300 "$work/$which.psql.err" | tr '\n' ' ')"
  fi
  log "$which: snapshot $snap on $host/$db"

  : > "$work/$which.counts.tsv"
  local t n
  for t in "${tables[@]}"; do
    echo "SELECT count(*) FROM $t;" >&"${SNAP[1]}"
    n=""
    read -r n <&"${SNAP[0]}" || true
    case "$n" in
      ''|*[!0-9]*) wait "$snap_pid" || true
                   die "$which: count of $t failed: $(head -c 300 "$work/$which.psql.err" | tr '\n' ' ')" ;;
    esac
    printf '%s\t%s\n' "$t" "$n" >> "$work/$which.counts.tsv"
    log "$which: $t rows=$n"
  done
  jq -R -n '[inputs | split("\t") | {key: .[0], value: (.[1] | tonumber)}] | from_entries' \
     "$work/$which.counts.tsv" > "$counts_json"

  PGPASSWORD="$password" pg_dump -h "$host" -p "$port" -U "$user" -d "$db" \
      -Fc --snapshot="$snap" -f "$out"
  log "$which: pg_dump -Fc -> $(basename "$out") bytes=$(file_bytes "$out")"

  echo "COMMIT;" >&"${SNAP[1]}"
  exec {SNAP[1]}>&-
  wait "$snap_pid" || die "$which: snapshot session ended with an error: $(head -c 300 "$work/$which.psql.err" | tr '\n' ' ')"
}

dump_db gw   "$GW_DB"   "$dir/gateway.dump" "$work/gw.counts.json"   "${GW_TABLES[@]}"
dump_db chat "$CHAT_DB" "$dir/sran_ai.dump" "$work/chat.counts.json" "${CHAT_TABLES[@]}"

# Uploads. A file removed between the directory scan and its read (the
# unattached-file sweep) makes tar exit 1 — a warning here, not a failure;
# any other tar exit code fails the run.
# FIX130 E1: gzip --rsyncable (same format, slightly larger) restarts the
# compressor at content-defined points, so a file added to the volume
# changes only the bytes around it and the off-site copy (restic, chunk
# dedup) uploads roughly the new file instead of the whole archive.
rc=0
tar -cf "$dir/uploads.tar.gz" -I 'gzip --rsyncable' --warning=no-file-changed --warning=no-file-removed -C "$UPLOADS_DIR" . || rc=$?
if [ $rc -eq 1 ]; then log "WARN uploads: tar reported files that changed during the read"
elif [ $rc -ne 0 ]; then die "uploads: tar failed rc=$rc"; fi
uploads_entries=$(tar -tzf "$dir/uploads.tar.gz" | wc -l | tr -d ' ')
log "uploads: tar | gzip --rsyncable -> uploads.tar.gz bytes=$(file_bytes "$dir/uploads.tar.gz") entries=$uploads_entries"

# manifest.json — everything drill.sh needs to prove the backup, and nothing
# secret (hosts/databases/users only).
file_entry() { jq -n --arg name "$1" --argjson bytes "$(file_bytes "$2")" --arg sha256 "$(file_sha256 "$2")" \
                 '{($name): {bytes: $bytes, sha256: $sha256}}'; }
{
  file_entry gateway.dump   "$dir/gateway.dump"
  file_entry sran_ai.dump   "$dir/sran_ai.dump"
  file_entry uploads.tar.gz "$dir/uploads.tar.gz"
} | jq -s 'add' > "$work/files.json"

jq -n \
  --arg id "$id" \
  --arg created_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --arg tz "${TZ:-UTC}" \
  --argjson duration_s "$(( $(date +%s) - t0 ))" \
  --arg pg_dump_version "$(pg_dump --version | sed 's/^pg_dump //')" \
  --arg gw_host "$GW_HOST" --arg gw_db "$GW_DB" --arg gw_user "$GW_USER" \
  --arg chat_host "$CHAT_HOST" --arg chat_db "$CHAT_DB" --arg chat_user "$CHAT_USER" \
  --argjson gw_counts "$(cat "$work/gw.counts.json")" \
  --argjson chat_counts "$(cat "$work/chat.counts.json")" \
  --argjson uploads_entries "$uploads_entries" \
  --argjson files "$(cat "$work/files.json")" \
  --argjson offsite_configured "$([ -n "${BACKUP_OFFSITE_REPOSITORY:-}" ] && echo true || echo false)" \
  '{
     schema: 1, id: $id, created_at: $created_at, tz: $tz, duration_s: $duration_s,
     pg_dump_version: $pg_dump_version,
     gateway: {host: $gw_host, database: $gw_db, user: $gw_user, file: "gateway.dump", counts: $gw_counts},
     chat:    {host: $chat_host, database: $chat_db, user: $chat_user, file: "sran_ai.dump", counts: $chat_counts},
     uploads: {file: "uploads.tar.gz", entries: $uploads_entries},
     files: $files,
     offsite: {configured: $offsite_configured}
   }' > "$dir/manifest.json"
log "manifest: $dir/manifest.json written"

# Prune. Only our own <YYYYMMDD_HHMMSS>[.failed] directories, by mtime.
pruned=0
while IFS= read -r old; do
  rm -rf "$old"
  pruned=$((pruned + 1))
  log "pruned $old (older than $KEEP_DAYS days)"
done < <(find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d \
           -regextype posix-extended -regex '.*/[0-9]{8}_[0-9]{6}(\.failed)?' \
           -mtime "+$KEEP_DAYS" 2>/dev/null | sort)

total=$(du -sb "$dir" | cut -f1)
log "backup done id=$id bytes=$total duration_s=$(( $(date +%s) - t0 )) pruned=$pruned"
[ -n "$RESULT_FILE" ] && printf '%s\n' "$dir" > "$RESULT_FILE"
exit 0
