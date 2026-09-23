#!/usr/bin/env bash
# FIX130 E4 — the backup service's alert (notify.sh) carries no secret, no
# SQL and no user text, and supports an ntfy access token.
#
# Runs INSIDE the backup image, with a stub `curl` first on PATH that records
# its argv and stdin instead of sending anything (no network, no real topic):
#
#   docker build -t fix130-ops-backup infra/backup
#   docker run --rm -v "$PWD/infra/backup/tests:/tests:ro" --entrypoint bash \
#     fix130-ops-backup /tests/test_notify.sh </dev/null
#
# Exit 0 = every check passed. Prints one PASS/FAIL line per check.
set -uo pipefail

T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
mkdir -p "$T/bin"
cat > "$T/bin/curl" <<'STUB'
#!/usr/bin/env bash
# Stub: record argv (one per line) and whatever arrives on stdin; answer OK.
{
  printf '%s\n' '--- ARGV'
  printf '%s\n' "$@"
  printf '%s\n' '--- STDIN'
  timeout 2 cat 2>/dev/null || true
  printf '\n%s\n' '--- END'
} >> "$CURL_LOG"
exit 0
STUB
chmod +x "$T/bin/curl"
export PATH="$T/bin:$PATH"

USER_TEXT="ขอเลื่อนนัดคุณสมชาย private-note-7f3a"
JWT="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJ1LTEyMyIsImp0aSI6ImFiYyJ9.c2lnbmF0dXJlLWJ5dGVzLWhlcmUtMTIzNDU2"
API_KEY="sk-live-9f8e7d6c5b4a39281706f5e4"
DSN="postgresql://app:hunter2pass@db.internal:5432/sran"
PLAIN_PW="Plain-Pass-42"
HOSTILE="pg_restore: error: could not execute query: UPDATE chat.messages_masked SET content = '$USER_TEXT' WHERE token = '$JWT'
connect $DSN failed; key $API_KEY; password was $PLAIN_PW"

# Values the leak scan looks for in everything curl was given.
LEAKS=("hunter2pass" "$JWT" "eyJhbGciOi" "$API_KEY" "private-note-7f3a" "สมชาย" "UPDATE chat" "SET content" "$PLAIN_PW")

fails=0
pass() { printf 'PASS %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1"; fails=$((fails + 1)); }

scan() {  # scan <name> <file> — no leak anywhere in what curl saw
  local name="$1" f="$2" hit="" x
  for x in "${LEAKS[@]}"; do
    if grep -qF -- "$x" "$f"; then hit="$hit [${x:0:14}]"; fi
  done
  if [ -z "$hit" ]; then pass "$name: no secret/SQL/user text reached curl"; else fail "$name: leaked$hit"; fi
}

run_notify() {  # run_notify <log> <env...> -- <level> <title> <message>
  local log="$1"; shift
  local envs=()
  while [ "$1" != "--" ]; do envs+=("$1"); shift; done
  shift
  : > "$log"
  env -i PATH="$PATH" HOME=/tmp CURL_LOG="$log" POSTGRES_PASSWORD="$PLAIN_PW" "${envs[@]}" \
    /notify.sh "$@" </dev/null >"$log.out" 2>&1
}

# 1. ntfy: body and Title header are scrubbed.
run_notify "$T/ntfy.log" ALERT_NTFY_URL=http://sink.invalid/fix130-topic -- \
  high "SRAN backup FAILED $DSN" "$HOSTILE"
if grep -qF -- "http://sink.invalid/fix130-topic" "$T/ntfy.log"; then pass "ntfy: posted to the configured topic"
else fail "ntfy: curl was not called with the topic"; fi
scan "ntfy" "$T/ntfy.log"
scan "ntfy log line" "$T/ntfy.log.out"
if grep -q -- '\[redacted\]' "$T/ntfy.log" && grep -q -- '\[sql redacted\]' "$T/ntfy.log"; then
  pass "ntfy: redaction markers present (the message still arrives, scrubbed)"
else fail "ntfy: expected [redacted] and [sql redacted] in the body"; fi

# 2. ntfy access token: Authorization header via curl config on stdin, never argv.
run_notify "$T/token.log" ALERT_NTFY_URL=http://sink.invalid/t ALERT_NTFY_TOKEN=tk_fix130dummytoken0123456789 -- \
  info "t" "m"
if sed -n '/^--- STDIN$/,/^--- END$/p' "$T/token.log" | grep -qF 'header = "Authorization: Bearer tk_fix130dummytoken0123456789"'; then
  pass "ntfy token: sent as Authorization: Bearer (curl config on stdin)"
else fail "ntfy token: no Authorization header"; fi
if sed -n '/^--- ARGV$/,/^--- STDIN$/p' "$T/token.log" | grep -qF 'tk_fix130dummytoken0123456789'; then
  fail "ntfy token: visible in curl argv"
else pass "ntfy token: not in argv"; fi
run_notify "$T/notoken.log" ALERT_NTFY_URL=http://sink.invalid/t -- info "t" "m"
if grep -q 'Authorization' "$T/notoken.log"; then fail "no token: an Authorization header was sent anyway"
else pass "no token: no Authorization header"; fi

# 3. e-mail (Resend): subject and text scrubbed; the API key only on stdin.
run_notify "$T/mail.log" RESEND_API_KEY=re_fix130dummykey0123456789 ALERT_EMAIL_TO=ops@example.com \
  RESEND_API_URL=http://sink.invalid/emails -- high "SRAN backup FAILED" "$HOSTILE"
scan "resend" "$T/mail.log"
if sed -n '/^--- ARGV$/,/^--- STDIN$/p' "$T/mail.log" | grep -qF 're_fix130dummykey'; then
  fail "resend: API key visible in curl argv"
else pass "resend: API key not in argv"; fi

echo "---"
if [ "$fails" -eq 0 ]; then echo "test_notify: ALL PASS"; exit 0; fi
echo "test_notify: $fails FAILED"
exit 1
