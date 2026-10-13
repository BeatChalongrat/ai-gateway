#!/usr/bin/env bash
# FIX130 E1 — prove the off-site copy end to end, entirely on throwaway
# containers (nothing of the live stack, the test stack or any real bucket /
# ntfy topic is touched):
#
#   fix130-ops-net         a throwaway network
#   fix130-ops-minio       MinIO = the S3-compatible bucket (bucket sran-offsite,
#                          a least-privilege user limited to that bucket)
#   fix130-ops-pg-gw       Postgres 16 seeded like the gateway DB (ai_gateway)
#   fix130-ops-pg-chat     Postgres 16 seeded like the chat DB (sran_ai, chat.*)
#   fix130-ops-sink        a dummy alert endpoint (sink.py) = ALERT_NTFY_URL
#   fix130-ops-uploads / fix130-ops-backups   throwaway volumes
#
# Scenarios: offsite-check (init + round trip) → `now` #1 (upload, off-site
# restore drill, retention, check) → more rows + a new upload → `now` #2
# (dedup) → restore on a "new machine" from the bucket alone (sha256 vs
# manifest + pg_restore --list) → wrong password → unreachable bucket →
# not configured. Assertions print PASS/FAIL; exit 0 = all passed.
#
#   scripts/ops/offsite_e2e.sh [image] [results dir]
#     image        default fix130-ops-backup (docker build -t fix130-ops-backup infra/backup)
#     results dir  default scripts/ops/results/ops
set -uo pipefail
export MSYS_NO_PATHCONV=1

IMAGE="${1:-fix130-ops-backup}"
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${2:-$HERE/results/ops}"
mkdir -p "$OUT"
P=fix130-ops
NET=$P-net
case "$IMAGE" in ai-gateway-*) echo "REFUSED: use a throwaway image, not $IMAGE" >&2; exit 2 ;; esac

winpath() { if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi; }
rand() { head -c 48 /dev/urandom | od -An -tx1 | tr -d ' \n' | head -c "${1:-32}"; }

fails=0
pass() { printf 'PASS %s\n' "$*" | tee -a "$OUT/e1_assertions.txt"; }
fail() { printf 'FAIL %s\n' "$*" | tee -a "$OUT/e1_assertions.txt"; fails=$((fails + 1)); }
note() { printf '%s\n' "$*" | tee -a "$OUT/e1_assertions.txt"; }

teardown() {
  [ "${KEEP:-0}" = 1 ] && [ "${1:-}" = exit ] && { echo "KEEP=1: throwaway containers left running (re-run without KEEP to clean)"; return; }
  docker rm -f $P-minio $P-pg-gw $P-pg-chat $P-sink >/dev/null 2>&1
  docker volume rm $P-uploads $P-backups $P-minio-data >/dev/null 2>&1
  docker network rm $NET >/dev/null 2>&1
  rm -f "${ENVF_BASE:-}" "${ENVF_OFF:-}" "${ENVF_WRONG:-}" "${ENVF_NOWHERE:-}" 2>/dev/null
}
trap "teardown exit" EXIT

: > "$OUT/e1_assertions.txt"
note "# FIX130 E1 off-site e2e — image $IMAGE — $(date -u +%FT%TZ)"
teardown
docker network create $NET >/dev/null

# --- secrets of this throwaway setup (never printed) -----------------------
GW_PW=$(rand); CHAT_PW=$(rand); MINIO_ROOT_PW=$(rand); S3_KEY_ID="fix130ops$(rand 8)"; S3_SECRET=$(rand 40)
RESTIC_PW=$(rand 40); NTFY_TOKEN="tk_fix130dummy$(rand 16)"
SECRETS=("$GW_PW" "$CHAT_PW" "$MINIO_ROOT_PW" "$S3_SECRET" "$RESTIC_PW" "$NTFY_TOKEN")

# --- MinIO + bucket + least-privilege user ---------------------------------
docker volume create $P-minio-data >/dev/null
docker run -d --name $P-minio --network $NET -e MINIO_ROOT_USER=fix130root -e MINIO_ROOT_PASSWORD="$MINIO_ROOT_PW" \
  -v $P-minio-data:/data minio/minio:latest server /data >/dev/null
cat > "$OUT/.policy.json" <<'JSON'
{"Version":"2012-10-17","Statement":[
 {"Effect":"Allow","Action":["s3:ListBucket","s3:GetBucketLocation"],"Resource":["arn:aws:s3:::sran-offsite"]},
 {"Effect":"Allow","Action":["s3:GetObject","s3:PutObject","s3:DeleteObject"],"Resource":["arn:aws:s3:::sran-offsite/*"]}]}
JSON
for i in $(seq 1 30); do
  docker run --rm --network $NET --entrypoint sh minio/mc:latest -c \
    "mc alias set m http://$P-minio:9000 fix130root '$MINIO_ROOT_PW' >/dev/null 2>&1" && break
  sleep 1
done
docker run --rm --network $NET -v "$(winpath "$OUT/.policy.json"):/policy.json:ro" --entrypoint sh minio/mc:latest -c "
  mc alias set m http://$P-minio:9000 fix130root '$MINIO_ROOT_PW' >/dev/null &&
  mc mb m/sran-offsite >/dev/null && mc mb m/some-other-bucket >/dev/null &&
  mc admin user add m '$S3_KEY_ID' '$S3_SECRET' >/dev/null &&
  mc admin policy create m sran-offsite-rw /policy.json >/dev/null &&
  mc admin policy attach m sran-offsite-rw --user '$S3_KEY_ID' >/dev/null && echo minio-ready" | tee -a "$OUT/e1_setup.log"
rm -f "$OUT/.policy.json"

# --- Postgres sources ------------------------------------------------------
docker run -d --name $P-pg-gw --network $NET -e POSTGRES_PASSWORD="$GW_PW" -e POSTGRES_DB=ai_gateway postgres:16 >/dev/null
docker run -d --name $P-pg-chat --network $NET -e POSTGRES_PASSWORD="$CHAT_PW" -e POSTGRES_USER=chatedge -e POSTGRES_DB=sran_ai postgres:16 >/dev/null
# The image's init runs a temporary server first; wait for the final one
# (the second "ready to accept connections").
for c in $P-pg-gw $P-pg-chat; do
  for i in $(seq 1 90); do
    [ "$(docker logs $c 2>&1 | grep -c 'ready to accept connections')" -ge 2 ] && break
    sleep 1
  done
done
docker exec -i $P-pg-gw psql -q -U postgres -d ai_gateway -v ON_ERROR_STOP=1 >/dev/null <<'SQL'
CREATE TABLE tenants (id serial primary key, name text);
CREATE TABLE portal_users (id serial primary key, email text);
CREATE TABLE request_logs (id serial primary key, model text, body text, created_at timestamptz default now());
CREATE TABLE credit_transactions (id serial primary key, amount numeric);
CREATE TABLE model_configs (id serial primary key, name text);
INSERT INTO tenants (name) SELECT 'tenant-' || g FROM generate_series(1, 5) g;
INSERT INTO portal_users (email) SELECT 'user' || g || '@example.com' FROM generate_series(1, 40) g;
INSERT INTO request_logs (model, body) SELECT 'model-' || (g % 7), md5(g::text) || md5((g * 31)::text) || md5((g * 17)::text) || md5((g * 13)::text) FROM generate_series(1, 200000) g;
INSERT INTO credit_transactions (amount) SELECT g * 0.01 FROM generate_series(1, 500) g;
INSERT INTO model_configs (name) SELECT 'model-' || g FROM generate_series(1, 7) g;
SQL
docker exec -i $P-pg-chat psql -q -U chatedge -d sran_ai -v ON_ERROR_STOP=1 >/dev/null <<'SQL'
CREATE SCHEMA chat;
CREATE TABLE chat.conversations (id serial primary key, title text);
CREATE TABLE chat.messages_masked (id serial primary key, conversation_id int, content text);
CREATE TABLE chat.conversation_files (id serial primary key, conversation_id int, name text);
INSERT INTO chat.conversations (title) SELECT 'conv ' || g FROM generate_series(1, 200) g;
INSERT INTO chat.messages_masked (conversation_id, content) SELECT (g % 200) + 1, md5(g::text) || md5((g * 3)::text) || md5((g * 5)::text) FROM generate_series(1, 20000) g;
INSERT INTO chat.conversation_files (conversation_id, name) SELECT g, 'file-' || g || '.pdf' FROM generate_series(1, 30) g;
SQL
docker volume create $P-uploads >/dev/null
docker volume create $P-backups >/dev/null
# ~25 MB of uploads over ~80 files (the live volume: 649 entries, 67 MB)
docker run --rm -v $P-uploads:/u alpine:3.20 sh -c 'for t in 1 2 3 4; do for c in 1 2 3 4 5 6 7 8 9 10; do mkdir -p /u/t$t/c$c; head -c $(( (t * 37 + c * 53) % 450 * 1000 + 50000 )) /dev/urandom > /u/t$t/c$c/file.bin; echo note-$t-$c > /u/t$t/c$c/note.txt; done; done; head -c 3000000 /dev/urandom > /u/t1/c1/report.pdf; head -c 1500000 /dev/urandom > /u/t2/photo.jpg'

# --- sink --------------------------------------------------------------------
docker run -d --name $P-sink --network $NET -v "$(winpath "$HERE/sink.py"):/sink.py:ro" python:3.12-slim python -u /sink.py >/dev/null

# --- env files (secrets on disk only here, removed on exit) ----------------
ENVF_BASE=$(mktemp); ENVF_OFF=$(mktemp); ENVF_WRONG=$(mktemp); ENVF_NOWHERE=$(mktemp)
cat > "$ENVF_BASE" <<EOF
TZ=Asia/Bangkok
POSTGRES_HOST=$P-pg-gw
POSTGRES_DB=ai_gateway
POSTGRES_USER=postgres
POSTGRES_PASSWORD=$GW_PW
WEBCHAT_POSTGRES_HOST=$P-pg-chat
WEBCHAT_POSTGRES_DB=sran_ai
WEBCHAT_POSTGRES_USER=chatedge
WEBCHAT_POSTGRES_PASSWORD=$CHAT_PW
ALERT_NTFY_URL=http://$P-sink:8080/fix130-dummy-topic
ALERT_NTFY_TOKEN=$NTFY_TOKEN
EOF
{ cat "$ENVF_BASE"; cat <<EOF
BACKUP_OFFSITE_REPOSITORY=s3:http://$P-minio:9000/sran-offsite/sran
BACKUP_OFFSITE_PASSWORD=$RESTIC_PW
BACKUP_OFFSITE_ACCESS_KEY_ID=$S3_KEY_ID
BACKUP_OFFSITE_SECRET_ACCESS_KEY=$S3_SECRET
BACKUP_OFFSITE_REGION=us-east-1
BACKUP_OFFSITE_CHECK_WEEKDAY=always
BACKUP_OFFSITE_CONNECT_TIMEOUT_S=60
EOF
} > "$ENVF_OFF"
sed "s|^BACKUP_OFFSITE_PASSWORD=.*|BACKUP_OFFSITE_PASSWORD=not-the-password|" "$ENVF_OFF" > "$ENVF_WRONG"
sed "s|^BACKUP_OFFSITE_REPOSITORY=.*|BACKUP_OFFSITE_REPOSITORY=s3:http://$P-nowhere:9000/sran-offsite/sran|" "$ENVF_OFF" > "$ENVF_NOWHERE"

# run_backup <label> <envfile> <args...> — logs to $OUT/e1_<label>.log, sets RC and SECS
run_backup() {
  local label="$1" envf="$2"; shift 2
  local t0; t0=$(date +%s)
  docker run --rm --name $P-backup-$label --network $NET --env-file "$(winpath "$envf")" \
    -v $P-uploads:/uploads:ro -v $P-backups:/backups "$IMAGE" "$@" > "$OUT/e1_$label.log" 2>&1
  RC=$?; SECS=$(( $(date +%s) - t0 ))
  note "--- $label: exit $RC in ${SECS}s ($(wc -l < "$OUT/e1_$label.log" | tr -d ' ') log lines)"
}
in_backups() { docker run --rm -v $P-backups:/backups --entrypoint bash "$IMAGE" -c "$1"; }
sink_lines() { docker logs $P-sink 2>/dev/null | grep -c '^{' ; }
bucket_objects() {
  docker run --rm --network $NET --entrypoint sh minio/mc:latest -c \
    "mc alias set m http://$P-minio:9000 fix130root '$MINIO_ROOT_PW' >/dev/null && mc ls --recursive m/sran-offsite | wc -l; mc du m/sran-offsite | tail -n1"
}

# 1. first-time setup
run_backup check "$ENVF_OFF" offsite-check
if [ $RC -eq 0 ] && grep -q 'restic init' "$OUT/e1_check.log" && grep -q 'round trip PASS' "$OUT/e1_check.log"; then
  pass "offsite-check: repository created on first use (restic init) + 64 KiB round trip"
else fail "offsite-check (rc=$RC)"; fi
grep -q 'test snapshot removed' "$OUT/e1_check.log" && pass "offsite-check: least-privilege key can delete (retention will work)" || fail "offsite-check: test snapshot not removed"

# 2. first real run
run_backup run1 "$ENVF_OFF" now
RUN1_SECS=$SECS
if [ $RC -eq 0 ] && grep -q 'offsite: copy PASS' "$OUT/e1_run1.log"; then pass "now #1: backup + drill + off-site copy PASS (exit 0)"; else fail "now #1 rc=$RC"; fi
D1=$(in_backups 'ls -1d /backups/2* | tail -n1')
OJ1=$(in_backups "cat $D1/offsite.json")
printf '%s\n' "$OJ1" > "$OUT/e1_run1_offsite.json"
in_backups "cat $D1/manifest.json" > "$OUT/e1_run1_manifest.json"
note "run1 dir=$D1 upload: $(printf '%s' "$OJ1" | jq -c '.upload') check: $(printf '%s' "$OJ1" | jq -c '.check')"
[ "$(printf '%s' "$OJ1" | jq '[.checks[] | select(.name | startswith("restore-sha256:")) | select(.pass)] | length')" = 4 ] \
  && pass "now #1: off-site restore drill — 3 files + manifest.json sha256 equal" || fail "now #1: restore sha256 checks"
[ "$(jq -r '.offsite.configured' < "$OUT/e1_run1_manifest.json")" = true ] && pass "manifest.json records offsite.configured=true" || fail "manifest offsite flag"
grep -q 'offsite: pass snapshot' "$OUT/e1_run1.log" && pass "summary line carries the off-site result: $(grep -o 'offsite: pass snapshot[^"]*' "$OUT/e1_run1.log" | tail -n1)" || fail "summary lacks offsite"

# 3. more data: rows in both DBs + one new upload
docker exec $P-pg-gw psql -q -U postgres -d ai_gateway -c "INSERT INTO request_logs (model, body) SELECT 'model-new', md5(g::text) || md5((g * 7)::text) FROM generate_series(1, 1000) g" >/dev/null
docker exec $P-pg-chat psql -q -U chatedge -d sran_ai -c "INSERT INTO chat.messages_masked (conversation_id, content) SELECT 1, repeat(md5((g*7)::text), 12) FROM generate_series(1, 50) g" >/dev/null
docker run --rm -v $P-uploads:/u alpine:3.20 sh -c 'mkdir -p /u/t9/c1 && head -c 400000 /dev/urandom > /u/t9/c1/new.png && head -c 200000 /dev/urandom > /u/t2/c3/generated.xlsx'
sleep 1

# 4. second run — dedup
run_backup run2 "$ENVF_OFF" now
if [ $RC -eq 0 ] && grep -q 'offsite: copy PASS' "$OUT/e1_run2.log"; then pass "now #2: PASS (exit 0)"; else fail "now #2 rc=$RC"; fi
D2=$(in_backups 'ls -1d /backups/2* | tail -n1')
OJ2=$(in_backups "cat $D2/offsite.json"); printf '%s\n' "$OJ2" > "$OUT/e1_run2_offsite.json"
ADDED1=$(printf '%s' "$OJ1" | jq '.upload.data_added'); TOTAL1=$(printf '%s' "$OJ1" | jq '.upload.total_bytes_processed')
ADDED2=$(printf '%s' "$OJ2" | jq '.upload.data_added'); TOTAL2=$(printf '%s' "$OJ2" | jq '.upload.total_bytes_processed')
note "dedup: run1 added $ADDED1 of $TOTAL1 bytes; run2 added $ADDED2 of $TOTAL2 bytes"
if [ "$ADDED2" -lt $(( TOTAL2 * 35 / 100 )) ]; then pass "now #2 deduplicated: uploaded $ADDED2 new bytes of $TOTAL2 processed ($(( 100 * ADDED2 / TOTAL2 )) %)"
else fail "now #2 dedup: $ADDED2 of $TOTAL2"; fi
note "bucket after 2 runs: $(bucket_objects | tr '\n' ' ')"

# 5. restore on a "new machine": the bucket + password only, no /backups volume
cat > "$OUT/.restore.sh" <<'EOS'
set -euo pipefail
export RESTIC_REPOSITORY="$BACKUP_OFFSITE_REPOSITORY" RESTIC_PASSWORD="$BACKUP_OFFSITE_PASSWORD"
export AWS_ACCESS_KEY_ID="$BACKUP_OFFSITE_ACCESS_KEY_ID" AWS_SECRET_ACCESS_KEY="$BACKUP_OFFSITE_SECRET_ACCESS_KEY" AWS_DEFAULT_REGION="$BACKUP_OFFSITE_REGION"
restic snapshots --host sran --tag sran-backup --compact
restic restore latest --host sran --tag sran-backup --target /restore --quiet
d=$(ls -1d /restore/backups/2* | tail -n1)
echo "restored $d"
for f in gateway.dump sran_ai.dump uploads.tar.gz; do
  want=$(jq -r --arg f "$f" '.files[$f].sha256' "$d/manifest.json"); got=$(sha256sum "$d/$f" | cut -d' ' -f1)
  [ "$want" = "$got" ] && echo "sha256 OK $f ${got:0:16}…" || { echo "sha256 MISMATCH $f"; exit 1; }
done
echo "gateway.dump TOC entries: $(pg_restore --list "$d/gateway.dump" | grep -c ' TABLE DATA ')"
echo "sran_ai.dump TOC entries: $(pg_restore --list "$d/sran_ai.dump" | grep -c ' TABLE DATA ')"
echo "uploads entries: $(tar -tzf "$d/uploads.tar.gz" | wc -l)"
echo "drill.json in the copy: pass=$(jq .pass "$d/drill.json")"
EOS
docker run --rm --name $P-newmachine --network $NET --env-file "$(winpath "$ENVF_OFF")" -v "$(winpath "$OUT/.restore.sh"):/restore.sh:ro" \
  --entrypoint bash "$IMAGE" /restore.sh > "$OUT/e1_restore_new_machine.log" 2>&1
RC=$?; rm -f "$OUT/.restore.sh"
LOCAL_SHA=$(in_backups "jq -r '.files[\"gateway.dump\"].sha256' $D2/manifest.json")
if [ $RC -eq 0 ] && [ "$(grep -c '^sha256 OK' "$OUT/e1_restore_new_machine.log")" = 3 ] && grep -q "${LOCAL_SHA:0:16}" "$OUT/e1_restore_new_machine.log"; then
  pass "restore on a new machine from the bucket alone: 3/3 sha256 match the manifest (gateway.dump = local copy ${LOCAL_SHA:0:12}…)"
else fail "new-machine restore rc=$RC"; fi

# 6. wrong password -> failure path + alert to the dummy sink
before=$(sink_lines)
run_backup wrongpw "$ENVF_WRONG" now
DW=$(in_backups 'ls -1d /backups/2* | tail -n1')
if [ $RC -eq 3 ] && grep -q 'wrong repository password' "$OUT/e1_wrongpw.log"; then pass "wrong password: exit 3, 'wrong repository password'"; else fail "wrong password rc=$RC"; fi
[ "$(in_backups "jq .pass $DW/drill.json")" = true ] && pass "wrong password: the local backup + drill still passed ($DW)" || fail "wrong password: local drill"
sleep 1
docker logs $P-sink 2>/dev/null | tail -n +$((before + 1)) | grep '^{' > "$OUT/e1_wrongpw_alert.json"
if grep -q 'SRAN off-site backup FAILED' "$OUT/e1_wrongpw_alert.json" && grep -q 'wrong repository password' "$OUT/e1_wrongpw_alert.json" \
   && grep -q '"authorization": "Bearer tk_fix130dummy' "$OUT/e1_wrongpw_alert.json"; then
  pass "wrong password: alert reached the dummy ntfy sink (priority $(jq -r .priority < "$OUT/e1_wrongpw_alert.json" | head -n1), Bearer token header)"
else fail "wrong password: alert"; fi
sed -i 's/"authorization": "Bearer tk_fix130dummy[^"]*"/"authorization": "Bearer tk_fix130dummy…(dummy, trimmed)"/' "$OUT/e1_wrongpw_alert.json"

# 7. unreachable bucket
before=$(sink_lines)
run_backup nowhere "$ENVF_NOWHERE" now
if [ $RC -eq 3 ] && grep -q 'offsite: FAIL connect' "$OUT/e1_nowhere.log"; then pass "unreachable bucket: exit 3 at step connect after ${SECS}s"; else fail "unreachable rc=$RC"; fi
sleep 1
docker logs $P-sink 2>/dev/null | tail -n +$((before + 1)) | grep '^{' > "$OUT/e1_nowhere_alert.json"
grep -q 'SRAN off-site backup FAILED' "$OUT/e1_nowhere_alert.json" && pass "unreachable bucket: alert sent" || fail "unreachable: alert"
sed -i 's/"authorization": "Bearer tk_fix130dummy[^"]*"/"authorization": "Bearer tk_fix130dummy…(dummy, trimmed)"/' "$OUT/e1_nowhere_alert.json"

# 8. not configured
before=$(sink_lines)
run_backup unconfigured "$ENVF_BASE" now
DU=$(in_backups 'ls -1d /backups/2* | tail -n1')
if [ $RC -eq 0 ] && [ "$(grep -c 'offsite copy: not configured' "$OUT/e1_unconfigured.log")" = 1 ]; then
  pass "not configured: exit 0, exactly one 'offsite copy: not configured' line"
else fail "not configured rc=$RC lines=$(grep -c 'offsite copy: not configured' "$OUT/e1_unconfigured.log")"; fi
[ "$(in_backups "jq .offsite.configured $DU/manifest.json")" = false ] && [ "$(in_backups "jq .configured $DU/offsite.json")" = false ] \
  && pass "not configured: manifest.offsite.configured=false, offsite.json configured=false" || fail "not configured: records"
[ "$(sink_lines)" = "$before" ] && pass "not configured: no alert" || fail "not configured: an alert was sent"
grep -q 'drill PASS' "$OUT/e1_unconfigured.log" && pass "not configured: local backup + drill PASS" || fail "not configured: drill"

# 9. no secret in any log or record
leak=""
for f in "$OUT"/e1_*; do
  for s in "${SECRETS[@]}"; do grep -qF -- "$s" "$f" && leak="$leak $(basename "$f")"; done
done
[ -z "$leak" ] && pass "no secret (DB passwords, S3 keys, restic password, ntfy token) in any log/record" || fail "secret found in:$leak"
grep -l 'sran-offsite' "$OUT"/e1_*.log >/dev/null 2>&1 && fail "bucket/path printed in: $(grep -l 'sran-offsite' "$OUT"/e1_*.log | xargs -n1 basename | paste -sd' ')" || pass "the repository is named by scheme + host only in every log (restic's own messages say <offsite repository>)"

note "--- total: $fails failure(s)"
exit $(( fails > 0 ))
