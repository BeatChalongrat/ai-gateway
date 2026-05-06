#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# SRAN AI Gateway — End-to-end PII pipeline test runner
#
# Usage:
#   export GATEWAY_KEY=gw_...
#   ./sran-pii-test.sh                    # run all scenarios
#   ./sran-pii-test.sh 3                  # run scenario #3 only
#   ./sran-pii-test.sh -v 2               # scenario #2, verbose (show full payload)
#   ./sran-pii-test.sh --list             # list scenarios
#
# What it tests (Beat's 4 questions):
#   1. MAP    — does PII get detected & masked?
#   2. LEAK   — masked payload is what reaches the provider (we use input_masked)
#   3. UNMAP  — does the response restore PII back correctly to user?
#   4. CONTEXT— with heavy masking, does the AI still answer coherently?
#
# NOT TESTED HERE (requires provider stub via pytest):
#   - Whitespace-mangled tokens from provider ([ EMAIL_0 ])
#     restore() uses exact-match str.replace() at transformer.py:431-463
#     → would fail silently. Test via pytest with mocked provider.
# ─────────────────────────────────────────────────────────────────────────────

set -uo pipefail

# Bash 4+ guard — declare -A, += on arrays, etc.
if (( BASH_VERSINFO[0] < 4 )); then
  echo "Error: bash 4+ required (found $BASH_VERSION)" >&2
  echo "  macOS users: brew install bash, then run with /usr/local/bin/bash $0" >&2
  exit 1
fi

GATEWAY_URL="${GATEWAY_URL:-http://localhost:8080}"
GATEWAY_KEY="${GATEWAY_KEY:-}"
MODEL="${MODEL:-gpt-4o-mini}"
ADMIN_LOG_PATH="${ADMIN_LOG_PATH:-/admin/requests}"   # adjust if different
ADMIN_USER="${ADMIN_USER:-admin}"
ADMIN_PASS="${ADMIN_PASS:-admin1234}"
VERBOSE=0
ONLY_SCENARIO=""

# Cookie jar for the admin session — populated by admin_login(), used by
# the trace-fetch step. Empty string means login failed / not attempted,
# in which case the trace section is skipped per scenario.
ADMIN_COOKIE_JAR=""

# ── Colors ────────────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'; C_DIM=$'\033[2m'
  C_RED=$'\033[31m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'
  C_BLUE=$'\033[34m'; C_MAGENTA=$'\033[35m'; C_CYAN=$'\033[36m'
  C_GRAY=$'\033[90m'; C_PURPLE=$'\033[95m'
else
  C_RESET=""; C_BOLD=""; C_DIM=""; C_RED=""; C_GREEN=""; C_YELLOW=""
  C_BLUE=""; C_MAGENTA=""; C_CYAN=""; C_GRAY=""; C_PURPLE=""
fi

# ── Argparse ──────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    -v|--verbose) VERBOSE=1; shift ;;
    --list) LIST_ONLY=1; shift ;;
    -h|--help)
      sed -n '2,22p' "$0"; exit 0 ;;
    *) ONLY_SCENARIO="$1"; shift ;;
  esac
done

# ── Dependency check ──────────────────────────────────────────────────────────
for cmd in curl jq; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "${C_RED}✗ Missing dependency: $cmd${C_RESET}" >&2
    echo "  Install: sudo apt install $cmd  (or brew install $cmd)" >&2
    exit 1
  fi
done

if [[ -z "$GATEWAY_KEY" && -z "${LIST_ONLY:-}" ]]; then
  echo "${C_RED}✗ GATEWAY_KEY not set${C_RESET}" >&2
  echo "  Run:  export GATEWAY_KEY=gw_..." >&2
  exit 1
fi

# ── Token → detector name map (gateway's detector registry) ──────────────────
# Used to assert that detectors expected per scenario actually fired in
# dlp_findings_json. Token names are the *user-facing* labels in
# SCENARIOS_EXPECT_PII; detector names match registry.py registrations.
declare -A TOKEN_TO_DETECTOR=(
  [EMAIL]="email"
  [PHONE]="phone"
  [THAI_ID]="thai_national_id"
  [DATE]="date_of_birth"
  [CREDIT_CARD]="credit_card"
  [ADDRESS]="thai_address"
  [THAI_NAME]="thai_name"
  [PASSPORT]="passport"
  [BANK_ACCOUNT]="thai_bank_account"
  [BEARER_TOKEN]="bearer_token"
  [CREDENTIAL]="api_key_secret"
)

# ── Scenarios ─────────────────────────────────────────────────────────────────
# Each scenario has: name, description, system prompt, user prompt,
# expected detectors (CSV of token names), expected HTTP status (CSV
# delimited by '|', e.g. "200" or "200|403"), max_tokens, stream flag.
declare -a SCENARIOS_NAME
declare -a SCENARIOS_DESC
declare -a SCENARIOS_SYS
declare -a SCENARIOS_USER
declare -a SCENARIOS_EXPECT_PII
declare -a SCENARIOS_EXPECT_STATUS
declare -a SCENARIOS_MAX_TOKENS
declare -a SCENARIOS_STREAM

add_scenario() {
  SCENARIOS_NAME+=("$1")
  SCENARIOS_DESC+=("$2")
  SCENARIOS_SYS+=("$3")
  SCENARIOS_USER+=("$4")
  SCENARIOS_EXPECT_PII+=("$5")
  SCENARIOS_EXPECT_STATUS+=("${6:-200}")
  SCENARIOS_MAX_TOKENS+=("${7:-500}")
  SCENARIOS_STREAM+=("${8:-0}")
}

# 1. Single PII — sanity check
add_scenario \
  "single_email" \
  "Single PII (email) — basic sanity check" \
  "You are a friendly customer support assistant. Reply briefly in the same language as the user." \
  "สวัสดีครับ ผมอยากให้ส่งใบเสร็จมาที่อีเมล somchai.j@example.com หน่อยครับ" \
  "EMAIL"

# 2. Heavy multi-PII — context stress (heavy echo → bigger token budget)
add_scenario \
  "heavy_multi_pii" \
  "Heavy mask (8+ PII types) — does AI still answer coherently?" \
  "You are a customer support agent. Acknowledge the request and confirm the details back to the user. Reply in Thai." \
  "ผมชื่อ นายสมชาย ใจดี บัตรประชาชน 1-1234-56789-01-2 อายุ 35 ปี เกิดวันที่ 15/03/1989 โทร 081-234-5678 อีเมล somchai.jaidee@gmail.com บัตรเครดิต 4532-1234-5678-9010 (Visa) ที่อยู่ 99/123 ซอยสุขุมวิท 31 แขวงคลองตันเหนือ เขตวัฒนา กรุงเทพ 10110 อยากเปลี่ยนแผน Premium เป็น Premium Plus และเปลี่ยนเบอร์ติดต่อเป็น 091-987-6543" \
  "EMAIL,PHONE,THAI_ID,DATE,CREDIT_CARD,ADDRESS,THAI_NAME" \
  "200" \
  "1500"

# 3. Duplicate PII — idempotency
add_scenario \
  "duplicate_pii" \
  "Same value appearing 2× — get distinct indices? restore both?" \
  "Reply briefly confirming the user's contact info back to them." \
  "Please send the report to alice@corp.com. Yes, alice@corp.com is correct. Also CC bob@corp.com." \
  "EMAIL"

# 4. Mixed Thai+English
add_scenario \
  "mixed_lang" \
  "Mixed Thai + English in same message" \
  "Reply in the same language as the user." \
  "Hi, my name is Dr. Somchai Jaidee, passport AB1234567. ผมจะเดินทางไปสนามบินดอนเมือง เบอร์ติดต่อ +66 89-555-6666 ครับ และอีเมล somchai.travel@gmail.com" \
  "PASSPORT,PHONE,EMAIL,THAI_NAME"

# 5. Context comprehension test — ask AI to do task that NEEDS the PII
add_scenario \
  "context_task" \
  "Heavy PII + task that requires understanding their roles — does AI follow logic?" \
  "You are a banking assistant. The user wants to transfer money. Confirm the transfer details by listing: sender name, sender account, recipient name, recipient account, and amount. Reply in Thai." \
  "ขอโอนเงินจาก คุณสมหญิง รักดี (อีเมล somying@corp.th บัญชี 123-4-56789-0) ไปให้ คุณวิชัย เก่งมาก (อีเมล wichai@corp.th บัญชี 987-6-54321-0) จำนวน 50,000 บาท ครับ" \
  "EMAIL,BANK_ACCOUNT,THAI_NAME"

# 6. Credentials — CRITICAL severity. Default profile MASKs, but if
# severity_threshold_block is set to CRITICAL, the request gets blocked
# (403). Accept either status — both are correct gateway behaviour.
add_scenario \
  "credentials" \
  "Bearer token + API key (CRITICAL category) — accept 200 (mask) or 403 (block)" \
  "You are a DevOps assistant. Help the user debug their config." \
  "I'm getting 401 errors when using Authorization: Bearer sk-proj-abc123XYZ456def789ghi012jkl345mno678pqr901stu and my OpenAI key OPENAI_API_KEY=sk-1234567890abcdefghijklmnopqrstuvwxyz isn't working. Help?" \
  "BEARER_TOKEN,CREDENTIAL" \
  "200|403"

# 7. Hallucination probe — ask AI to repeat a placeholder verbatim
add_scenario \
  "placeholder_handling" \
  "Does AI handle [EMAIL_0]-style placeholders correctly in its reply?" \
  "Repeat back to the user exactly what they wrote. Do not add commentary." \
  "Contact me at jane.doe@startup.io or +1-555-123-4567. Repeat my contact info." \
  "EMAIL,PHONE"

# 8. Round-trip integrity — ask AI to echo verbatim (echo needs headroom)
add_scenario \
  "echo_test" \
  "Round-trip integrity — AI echoes input, restore must rebuild original" \
  "Echo the user's message back to them word-for-word, exactly as written." \
  "My ID is 1-1234-56789-01-2 and my phone is 089-999-8888." \
  "THAI_ID,PHONE" \
  "200" \
  "1500"

# 9. Streaming — exercises the rolling-tail buffer in public_chat.py:278-420.
# Long email + phone in one message; restore must round-trip across SSE chunks.
add_scenario \
  "streaming_basic" \
  "Streaming path — does rolling-tail buffer restore correctly?" \
  "Reply with a short confirmation in Thai." \
  "ส่งใบเสร็จไปที่ alice.smith.long.email@verylongdomain.example.com ด้วยครับ และโทรกลับ 081-234-5678" \
  "EMAIL,PHONE" \
  "200" \
  "1500" \
  "1"

# 10. Cache hit detection — simple deterministic prompt, repeated 2×.
# The runner reuses this scenario name to drive the cache test below.
add_scenario \
  "cache_hit" \
  "Cache hit detection — repeat identical request" \
  "Reply briefly." \
  "What is 2+2?" \
  "" \
  "200"

NUM_SCENARIOS=${#SCENARIOS_NAME[@]}

# ── List mode ────────────────────────────────────────────────────────────────
if [[ -n "${LIST_ONLY:-}" ]]; then
  echo "${C_BOLD}Available scenarios:${C_RESET}"
  for i in $(seq 0 $((NUM_SCENARIOS - 1))); do
    printf "  ${C_CYAN}%d${C_RESET}  ${C_BOLD}%-22s${C_RESET}  %s\n" \
      $((i+1)) "${SCENARIOS_NAME[$i]}" "${SCENARIOS_DESC[$i]}"
  done
  exit 0
fi

# ── Helpers ───────────────────────────────────────────────────────────────────
banner() {
  local title="$1"
  local color="${2:-$C_CYAN}"
  local width=78
  printf "\n${color}${C_BOLD}"
  printf '═%.0s' $(seq 1 $width); echo
  printf "  %s\n" "$title"
  printf '═%.0s' $(seq 1 $width); echo
  printf "${C_RESET}"
}

section() {
  printf "\n${C_BLUE}${C_BOLD}┌─ %s ${C_RESET}\n" "$1"
}

kv() {
  printf "  ${C_DIM}%-18s${C_RESET} %s\n" "$1" "$2"
}

mark() {
  case "$1" in
    pass) printf "${C_GREEN}✓${C_RESET}" ;;
    fail) printf "${C_RED}✗${C_RESET}" ;;
    warn) printf "${C_YELLOW}⚠${C_RESET}" ;;
    info) printf "${C_BLUE}ℹ${C_RESET}" ;;
  esac
}

# Highlight PII tokens [EMAIL_0], [PHONE_NUMBER_1], etc. in colored output
highlight_tokens() {
  # bash regex-aware sed — match [WORD_DIGITS] or [WORD]
  sed -E "s/(\[[A-Z_]+(_[0-9]+)?\])/${C_MAGENTA}${C_BOLD}\1${C_RESET}/g"
}

# Check if string contains any of the literal values from a list
contains_any() {
  local haystack="$1"
  shift
  for needle in "$@"; do
    [[ -z "$needle" ]] && continue
    if [[ "$haystack" == *"$needle"* ]]; then
      echo "$needle"
      return 0
    fi
  done
  return 1
}

# Extract PII values that should never appear in masked payload
extract_pii_values() {
  local text="$1"
  # naive but useful — extract emails, phones, IDs, credit cards
  echo "$text" | grep -oE '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' || true
  echo "$text" | grep -oE '\b0[689][0-9]{1}[-]?[0-9]{3,4}[-]?[0-9]{4}\b' || true
  # International phones (+66, +1, ...) — covers scenario 4 (+66 89-555-6666)
  # and scenario 7 (+1-555-123-4567) which the Thai-mobile pattern misses.
  echo "$text" | grep -oE '\+[0-9]{1,3}[-[:space:]]?[0-9]{2,4}[-[:space:]]?[0-9]{3,4}[-[:space:]]?[0-9]{4}' || true
  echo "$text" | grep -oE '\b[0-9]{1}-[0-9]{4}-[0-9]{5}-[0-9]{2}-[0-9]{1}\b' || true
  echo "$text" | grep -oE '\b[0-9]{4}-[0-9]{4}-[0-9]{4}-[0-9]{4}\b' || true
  echo "$text" | grep -oE '\b[0-9]{3}-[0-9]{1}-[0-9]{5}-[0-9]{1}\b' || true
  echo "$text" | grep -oE '\b[A-Z]{1,2}[0-9]{6,8}\b' || true
}

# ── Admin login (cookie-based) ────────────────────────────────────────────────
# /auth/login is 410 Gone (auth.py:109); /auth/session/login is the live
# endpoint and is cookie-based, NOT JWT. Bearer tokens on admin routes
# are rejected with 401 (deps.py:175-181). So we capture the cookies in
# a temp jar and pass them to the trace-fetch step.
admin_login() {
  local jar resp http_code
  jar=$(mktemp)
  http_code=$(curl -sS -o /dev/null -w "%{http_code}" \
    -c "$jar" \
    -X POST "$GATEWAY_URL/auth/session/login" \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"$ADMIN_USER\",\"password\":\"$ADMIN_PASS\"}" 2>&1) || true
  # Verify the cookie jar actually has the session cookie before declaring success.
  if [[ "$http_code" == "200" ]] && grep -q 'admin_session' "$jar" 2>/dev/null; then
    echo "$jar"
    return 0
  fi
  rm -f "$jar"
  return 1
}

# ── Run a single scenario ─────────────────────────────────────────────────────
run_scenario() {
  local idx="$1"
  local name="${SCENARIOS_NAME[$idx]}"
  local desc="${SCENARIOS_DESC[$idx]}"
  local sys="${SCENARIOS_SYS[$idx]}"
  local usr="${SCENARIOS_USER[$idx]}"
  local expect_pii="${SCENARIOS_EXPECT_PII[$idx]}"
  local expect_status="${SCENARIOS_EXPECT_STATUS[$idx]}"
  local max_tok="${SCENARIOS_MAX_TOKENS[$idx]}"
  local stream_flag="${SCENARIOS_STREAM[$idx]}"
  local req_id="sran-test-$(date +%s)-$idx"

  banner "Scenario $((idx+1))/${NUM_SCENARIOS}: $name"
  printf "${C_DIM}%s${C_RESET}\n" "$desc"

  # ── Build payload ──────────────────────────────────────────────────────────
  local stream_bool="false"
  [[ "$stream_flag" == "1" ]] && stream_bool="true"

  # File-based payload: $(jq ...) capture corrupts non-ASCII bytes on
  # Windows MSYS bash (Thai UTF-8 → 0x3F '?'). See docs/known-issues/
  # dlp-detector-gaps.md issue #8. jq writes JSON straight to disk; curl
  # reads with --data-binary to skip any shell text handling.
  local payload_file
  payload_file=$(mktemp --suffix=.json)
  jq -n \
    --arg model "$MODEL" \
    --arg sys "$sys" \
    --arg usr "$usr" \
    --argjson max_tok "$max_tok" \
    --argjson stream "$stream_bool" \
    '{
       model: $model,
       messages: [
         {role: "system", content: $sys},
         {role: "user",   content: $usr}
       ],
       temperature: 0.3,
       max_tokens: $max_tok,
       stream: $stream
     }' > "$payload_file"

  # ── Show original input ────────────────────────────────────────────────────
  section "ORIGINAL USER INPUT"
  printf "  %s\n" "$usr"

  # ── Detect PII values in original (for leak check) ─────────────────────────
  local pii_values
  pii_values=$(extract_pii_values "$usr" | sort -u | grep -v '^$' || true)

  if [[ -n "$pii_values" ]]; then
    section "RAW PII VALUES (must NOT appear in masked payload)"
    while IFS= read -r v; do
      [[ -z "$v" ]] && continue
      printf "  ${C_RED}● ${C_RESET}%s\n" "$v"
    done <<< "$pii_values"
  fi

  # ── Send request ───────────────────────────────────────────────────────────
  section "SENDING → $GATEWAY_URL/v1/chat/completions"
  kv "model"          "$MODEL"
  kv "request_id"     "$req_id"
  kv "expected_pii"   "$expect_pii"
  kv "expected_status" "$expect_status"
  kv "stream"         "$stream_bool"
  kv "max_tokens"     "$max_tok"

  local t_start t_end elapsed_ms
  t_start=$(date +%s%3N 2>/dev/null || python3 -c 'import time; print(int(time.time()*1000))')

  local resp_file headers_file http_code
  resp_file=$(mktemp); headers_file=$(mktemp)
  trap 'rm -f "$resp_file" "$headers_file" "$payload_file"' RETURN

  http_code=$(curl -sS -o "$resp_file" -D "$headers_file" -w "%{http_code}" \
    -X POST "$GATEWAY_URL/v1/chat/completions" \
    -H "Authorization: Bearer $GATEWAY_KEY" \
    -H "Content-Type: application/json" \
    -H "X-Request-Id: $req_id" \
    --data-binary @"$payload_file" 2>&1) || true

  t_end=$(date +%s%3N 2>/dev/null || python3 -c 'import time; print(int(time.time()*1000))')
  elapsed_ms=$((t_end - t_start))

  local server_req_id
  server_req_id=$(grep -i '^x-request-id:' "$headers_file" 2>/dev/null | awk '{print $2}' | tr -d '\r' || echo "")

  section "RESPONSE"
  kv "http_status" "$http_code"
  kv "latency"     "${elapsed_ms}ms"
  [[ -n "$server_req_id" ]] && kv "x-request-id" "$server_req_id"

  # Status check — accept any of the | -delimited expected values.
  if [[ "|$expect_status|" == *"|$http_code|"* ]]; then
    printf "  $(mark pass) ${C_GREEN}HTTP %s (expected: %s)${C_RESET}\n" "$http_code" "$expect_status"
  else
    printf "\n  $(mark fail) ${C_RED}HTTP %s — expected one of %s${C_RESET}\n" "$http_code" "$expect_status"
    section "RAW RESPONSE BODY"
    jq -C '.' < "$resp_file" 2>/dev/null || cat "$resp_file"
    return 1
  fi

  # If we expected and got a non-200 (e.g. 403 DLP block), there's no
  # provider response body to verify — exit early but don't fail.
  if [[ "$http_code" != "200" ]]; then
    section "BLOCKED RESPONSE BODY"
    jq -C '.' < "$resp_file" 2>/dev/null || cat "$resp_file"
    return 0
  fi

  # ── Parse response ─────────────────────────────────────────────────────────
  local final_content
  if [[ "$stream_flag" == "1" ]]; then
    # SSE stream: each event is `data: {json}\n\n`. Concatenate
    # delta.content across all events and drop the trailing [DONE].
    final_content=$(grep -E '^data: ' "$resp_file" \
      | sed -E 's/^data: //' \
      | grep -v '^\[DONE\]$' \
      | jq -r '.choices[0].delta.content // empty' 2>/dev/null \
      | tr -d '\n')
  else
    final_content=$(jq -r '.choices[0].message.content // ""' < "$resp_file")
  fi

  section "FINAL CONTENT (after restore — what user sees)"
  printf "  %s\n" "$final_content" | highlight_tokens

  # ── Round-trip & leak checks (heuristic, from response only) ──────────────
  section "VERIFICATION"

  # 1. UNMAP — did final content still contain placeholder tokens?
  local orphan_tokens
  orphan_tokens=$(echo "$final_content" | grep -oE '\[[A-Z_]+(_[0-9]+)?\]' | sort -u || true)
  if [[ -n "$orphan_tokens" ]]; then
    printf "  $(mark warn) ${C_YELLOW}Orphan tokens in final output:${C_RESET}\n"
    while IFS= read -r t; do
      printf "      ${C_MAGENTA}%s${C_RESET}\n" "$t"
    done <<< "$orphan_tokens"
    printf "  ${C_DIM}      → could be: hallucinated token OR malformed restore${C_RESET}\n"
  else
    printf "  $(mark pass) ${C_GREEN}No orphan tokens in final output${C_RESET}\n"
  fi

  # 2. CONTEXT — did AI actually respond meaningfully? (length heuristic)
  local content_len=${#final_content}
  if (( content_len < 20 )); then
    printf "  $(mark warn) ${C_YELLOW}Final content very short (${content_len} chars) — AI may have failed to respond${C_RESET}\n"
  else
    printf "  $(mark pass) ${C_GREEN}AI produced ${content_len}-char response${C_RESET}\n"
  fi

  # 3. ECHO/REPEAT scenarios — check that PII came back
  if [[ "$name" == "echo_test" || "$name" == "placeholder_handling" || "$name" == "duplicate_pii" ]]; then
    if [[ -n "$pii_values" ]]; then
      local restored_count=0 missing_count=0 missing_list=""
      while IFS= read -r v; do
        [[ -z "$v" ]] && continue
        if [[ "$final_content" == *"$v"* ]]; then
          restored_count=$((restored_count + 1))
        else
          missing_count=$((missing_count + 1))
          missing_list="$missing_list $v"
        fi
      done <<< "$pii_values"

      if (( missing_count == 0 && restored_count > 0 )); then
        printf "  $(mark pass) ${C_GREEN}Round-trip OK — all %d PII value(s) restored in final${C_RESET}\n" "$restored_count"
      elif (( restored_count > 0 )); then
        printf "  $(mark warn) ${C_YELLOW}Partial restore: %d ok / %d missing${C_RESET}\n" "$restored_count" "$missing_count"
        for v in $missing_list; do
          printf "      ${C_DIM}missing: %s${C_RESET}\n" "$v"
        done
      else
        printf "  $(mark info) ${C_BLUE}AI didn't echo PII back (may be model behavior, not gateway issue)${C_RESET}\n"
      fi
    fi
  fi

  # ── Verbose: full payload + headers ──────────────────────────────────────
  if (( VERBOSE )); then
    section "FULL RESPONSE JSON"
    if [[ "$stream_flag" == "1" ]]; then
      printf "  ${C_DIM}(streaming — raw SSE body)${C_RESET}\n"
      sed 's/^/  /' < "$resp_file"
    else
      jq -C '.' < "$resp_file"
    fi
    section "RESPONSE HEADERS"
    cat "$headers_file" | sed 's/^/  /'
  fi

  # ── Try to fetch admin trace (if endpoint exists) ─────────────────────────
  # Track scenario-level pass/fail for detector coverage (set inside the
  # trace block; checked at function return).
  local detector_check_failed=0

  if [[ -n "$server_req_id" && -n "$ADMIN_COOKIE_JAR" ]]; then
    section "INSPECTING SERVER-SIDE TRACE"
    printf "  ${C_DIM}Attempting GET %s/%s${C_RESET}\n" "$ADMIN_LOG_PATH" "$server_req_id"
    local trace_file trace_code
    trace_file=$(mktemp)
    trace_code=$(curl -sS -o "$trace_file" -w "%{http_code}" \
      -b "$ADMIN_COOKIE_JAR" \
      "$GATEWAY_URL$ADMIN_LOG_PATH/$server_req_id" 2>&1) || trace_code="000"

    if [[ "$trace_code" == "200" ]]; then
      local input_masked output_raw output_final findings_count input_findings
      input_masked=$(jq -r '.input_masked // "—"' < "$trace_file" 2>/dev/null)
      output_raw=$(jq -r '.output_raw // "—"' < "$trace_file" 2>/dev/null)
      output_final=$(jq -r '.output_final // "—"' < "$trace_file" 2>/dev/null)
      input_findings=$(jq -r '.dlp_input_findings // 0' < "$trace_file" 2>/dev/null)
      findings_count=$(jq -r '.dlp_findings_json // "[]" | fromjson | length' < "$trace_file" 2>/dev/null || echo "?")

      printf "\n  $(mark info) ${C_BOLD}MAP — Detection${C_RESET}\n"
      kv "input_findings" "$input_findings"
      kv "total_findings" "$findings_count"

      # ── DETECTOR COVERAGE — assert expect_pii against fired detectors ────
      if [[ -n "$expect_pii" ]]; then
        local detected_detectors
        detected_detectors=$(jq -r '.dlp_findings_json // "[]" | fromjson | .[].detector' < "$trace_file" 2>/dev/null | sort -u)

        IFS=',' read -ra expected_tokens <<< "$expect_pii"
        local missing_detectors=()
        for tok in "${expected_tokens[@]}"; do
          local det="${TOKEN_TO_DETECTOR[$tok]:-}"
          if [[ -z "$det" ]]; then
            printf "      ${C_DIM}skip unmapped token: %s${C_RESET}\n" "$tok"
            continue
          fi
          if ! echo "$detected_detectors" | grep -q "^${det}$"; then
            missing_detectors+=("$tok→$det")
          fi
        done

        if (( ${#missing_detectors[@]} == 0 )); then
          printf "  $(mark pass) ${C_GREEN}DETECTOR COVERAGE — all expected detectors fired${C_RESET}\n"
        else
          printf "  $(mark fail) ${C_RED}DETECTOR COVERAGE — missing:${C_RESET}\n"
          for m in "${missing_detectors[@]}"; do
            printf "      ${C_RED}%s${C_RESET}\n" "$m"
          done
          detector_check_failed=1
        fi
      fi

      printf "\n  $(mark info) ${C_BOLD}MAP — Masked payload sent to provider${C_RESET}\n"
      printf "  ${C_DIM}┄┄┄ input_masked ┄┄┄${C_RESET}\n"
      printf "  %s\n" "$input_masked" | highlight_tokens | sed 's/^/  /'

      # ── LEAK CHECK on masked payload ─────────────────────────────────────
      if [[ -n "$pii_values" && "$input_masked" != "—" ]]; then
        local leaked
        leaked=$(contains_any "$input_masked" $pii_values || true)
        if [[ -n "$leaked" ]]; then
          printf "\n  $(mark fail) ${C_RED}${C_BOLD}LEAK DETECTED${C_RESET} — raw PII in masked payload:\n"
          printf "      ${C_RED}%s${C_RESET}\n" "$leaked"
        else
          printf "\n  $(mark pass) ${C_GREEN}LEAK CHECK — no raw PII in payload sent to provider${C_RESET}\n"
        fi
      fi

      printf "\n  $(mark info) ${C_BOLD}UNMAP — Raw provider response (pre-restore)${C_RESET}\n"
      printf "  %s\n" "$output_raw" | highlight_tokens | sed 's/^/  /'

      printf "\n  $(mark info) ${C_BOLD}UNMAP — Final output (post-restore)${C_RESET}\n"
      printf "  %s\n" "$output_final" | sed 's/^/  /'

      # ── Restore correctness ──────────────────────────────────────────────
      local raw_tokens final_tokens
      raw_tokens=$(echo "$output_raw" | grep -oE '\[[A-Z_]+(_[0-9]+)?\]' | sort -u || true)
      final_tokens=$(echo "$output_final" | grep -oE '\[[A-Z_]+(_[0-9]+)?\]' | sort -u || true)

      if [[ -n "$raw_tokens" && -z "$final_tokens" ]]; then
        printf "\n  $(mark pass) ${C_GREEN}RESTORE — all tokens restored${C_RESET}\n"
      elif [[ -n "$raw_tokens" && -n "$final_tokens" ]]; then
        printf "\n  $(mark warn) ${C_YELLOW}RESTORE — some tokens orphaned in final${C_RESET}\n"
        printf "      raw    : %s\n" "$(echo "$raw_tokens" | tr '\n' ' ')"
        printf "      final  : %s ${C_DIM}(should be empty)${C_RESET}\n" "$(echo "$final_tokens" | tr '\n' ' ')"
      elif [[ -z "$raw_tokens" ]]; then
        printf "\n  $(mark info) ${C_BLUE}RESTORE — provider didn't use placeholders in response${C_RESET}\n"
        printf "      ${C_DIM}context preservation note: AI rephrased/summarized without echoing tokens${C_RESET}\n"
      fi

      if (( VERBOSE )); then
        section "FULL ADMIN TRACE"
        jq -C '.' < "$trace_file"
      fi
    elif [[ "$trace_code" == "404" ]]; then
      printf "  ${C_DIM}404 — adjust ADMIN_LOG_PATH env var (current: %s)${C_RESET}\n" "$ADMIN_LOG_PATH"
    elif [[ "$trace_code" == "401" || "$trace_code" == "403" ]]; then
      printf "  ${C_DIM}%s — admin session expired or insufficient role; trace skipped${C_RESET}\n" "$trace_code"
    else
      printf "  ${C_DIM}HTTP %s — trace unavailable${C_RESET}\n" "$trace_code"
    fi
    rm -f "$trace_file"
  elif [[ -n "$server_req_id" ]]; then
    section "INSPECTING SERVER-SIDE TRACE"
    printf "  ${C_DIM}admin login unavailable — trace inspection disabled${C_RESET}\n"
    printf "  ${C_DIM}set ADMIN_USER / ADMIN_PASS or check /auth/session/login is reachable${C_RESET}\n"
  fi

  rm -f "$resp_file" "$headers_file"
  if (( detector_check_failed )); then
    return 1
  fi
  return 0
}

# ── Cache hit verification ────────────────────────────────────────────────────
# Sends the cache_hit scenario twice (back-to-back) and reports the
# latency ratio. A second-call latency under 30% of the first is taken
# as a likely cache hit. For a stronger signal, set ADMIN_USER/PASS so
# the trace fetch can compare output_raw/output_final byte-for-byte.
run_cache_hit_test() {
  local idx
  for i in $(seq 0 $((NUM_SCENARIOS - 1))); do
    if [[ "${SCENARIOS_NAME[$i]}" == "cache_hit" ]]; then
      idx=$i
      break
    fi
  done
  if [[ -z "${idx:-}" ]]; then
    return 0
  fi

  banner "Cache hit verification — running scenario '${SCENARIOS_NAME[$idx]}' twice" "$C_PURPLE"

  local sys="${SCENARIOS_SYS[$idx]}"
  local usr="${SCENARIOS_USER[$idx]}"
  # File-based payload — see issue #8 in docs/known-issues/dlp-detector-gaps.md.
  local payload_file
  payload_file=$(mktemp --suffix=.json)
  trap 'rm -f "$payload_file"' RETURN
  jq -n \
    --arg model "$MODEL" \
    --arg sys "$sys" \
    --arg usr "$usr" \
    '{
       model: $model,
       messages: [
         {role: "system", content: $sys},
         {role: "user",   content: $usr}
       ],
       temperature: 0.3,
       max_tokens: 200
     }' > "$payload_file"

  local _hit_call
  _hit_call() {
    local label="$1"
    local req_id="sran-cache-${label}-$(date +%s)"
    local resp_file headers_file http_code
    resp_file=$(mktemp); headers_file=$(mktemp)
    local t_start t_end elapsed_ms
    t_start=$(date +%s%3N 2>/dev/null || python3 -c 'import time; print(int(time.time()*1000))')
    http_code=$(curl -sS -o "$resp_file" -D "$headers_file" -w "%{http_code}" \
      -X POST "$GATEWAY_URL/v1/chat/completions" \
      -H "Authorization: Bearer $GATEWAY_KEY" \
      -H "Content-Type: application/json" \
      -H "X-Request-Id: $req_id" \
      --data-binary @"$payload_file" 2>&1) || true
    t_end=$(date +%s%3N 2>/dev/null || python3 -c 'import time; print(int(time.time()*1000))')
    elapsed_ms=$((t_end - t_start))
    local server_req_id
    server_req_id=$(grep -i '^x-request-id:' "$headers_file" 2>/dev/null | awk '{print $2}' | tr -d '\r' || echo "")
    echo "${http_code}|${elapsed_ms}|${server_req_id}|${resp_file}|${headers_file}"
  }

  section "Call 1 (cold)"
  local r1 r1_status r1_latency r1_reqid r1_resp r1_hdr
  r1=$(_hit_call "1")
  IFS='|' read -r r1_status r1_latency r1_reqid r1_resp r1_hdr <<< "$r1"
  kv "http_status" "$r1_status"
  kv "latency"     "${r1_latency}ms"
  kv "request_id"  "$r1_reqid"

  # Brief gap to be sure the cache write completed.
  sleep 1

  section "Call 2 (potentially cached)"
  local r2 r2_status r2_latency r2_reqid r2_resp r2_hdr
  r2=$(_hit_call "2")
  IFS='|' read -r r2_status r2_latency r2_reqid r2_resp r2_hdr <<< "$r2"
  kv "http_status" "$r2_status"
  kv "latency"     "${r2_latency}ms"
  kv "request_id"  "$r2_reqid"

  section "VERIFICATION"
  if [[ "$r1_status" != "200" || "$r2_status" != "200" ]]; then
    printf "  $(mark warn) ${C_YELLOW}One of the calls didn't return 200 — cache check skipped${C_RESET}\n"
  else
    local ratio_pct
    if (( r1_latency > 0 )); then
      ratio_pct=$(( r2_latency * 100 / r1_latency ))
    else
      ratio_pct=100
    fi
    kv "ratio (call2/1)" "${ratio_pct}%"
    if (( ratio_pct < 30 )); then
      printf "  $(mark pass) ${C_GREEN}Likely CACHE HIT — call 2 was %d%% of call 1${C_RESET}\n" "$ratio_pct"
    else
      printf "  $(mark info) ${C_BLUE}No clear cache hit signature (ratio %d%%) — could be small response or cache disabled${C_RESET}\n" "$ratio_pct"
    fi

    # If admin trace is available, compare output_raw byte-for-byte —
    # exact match on the stored provider response strongly implies a
    # cache hit (cache_service stores the post-restore response).
    if [[ -n "$ADMIN_COOKIE_JAR" && -n "$r1_reqid" && -n "$r2_reqid" ]]; then
      local t1 t2 c1 c2 out1 out2
      t1=$(mktemp); t2=$(mktemp)
      c1=$(curl -sS -o "$t1" -w "%{http_code}" -b "$ADMIN_COOKIE_JAR" \
        "$GATEWAY_URL$ADMIN_LOG_PATH/$r1_reqid" 2>&1) || c1="000"
      c2=$(curl -sS -o "$t2" -w "%{http_code}" -b "$ADMIN_COOKIE_JAR" \
        "$GATEWAY_URL$ADMIN_LOG_PATH/$r2_reqid" 2>&1) || c2="000"
      if [[ "$c1" == "200" && "$c2" == "200" ]]; then
        out1=$(jq -r '.output_raw // ""' < "$t1" 2>/dev/null)
        out2=$(jq -r '.output_raw // ""' < "$t2" 2>/dev/null)
        if [[ -n "$out1" && "$out1" == "$out2" ]]; then
          printf "  $(mark pass) ${C_GREEN}TRACE CONFIRM — output_raw byte-equal across calls${C_RESET}\n"
        else
          printf "  $(mark info) ${C_BLUE}TRACE — output_raw differs across calls (cache may not have hit)${C_RESET}\n"
        fi
      fi
      rm -f "$t1" "$t2"
    fi
  fi

  rm -f "$r1_resp" "$r1_hdr" "$r2_resp" "$r2_hdr"
}

# ── Main ──────────────────────────────────────────────────────────────────────
banner "SRAN AI Gateway — PII Pipeline Test Runner" "$C_PURPLE"
kv "gateway_url"  "$GATEWAY_URL"
kv "model"        "$MODEL"
kv "scenarios"    "$NUM_SCENARIOS"
kv "key_prefix"   "${GATEWAY_KEY:0:8}..."

# ── Bootstrap admin session for trace inspection ──────────────────────────────
printf "\n${C_DIM}Logging in to %s as %s for trace inspection...${C_RESET}\n" \
  "$GATEWAY_URL/auth/session/login" "$ADMIN_USER"
if ADMIN_COOKIE_JAR=$(admin_login); then
  kv "admin_login" "${C_GREEN}ok${C_RESET} (cookie jar: $ADMIN_COOKIE_JAR)"
  trap 'rm -f "$ADMIN_COOKIE_JAR"' EXIT
else
  ADMIN_COOKIE_JAR=""
  printf "  ${C_YELLOW}⚠ admin login failed — trace inspection disabled${C_RESET}\n"
  printf "  ${C_DIM}check ADMIN_USER/ADMIN_PASS, or that POST /auth/session/login is reachable${C_RESET}\n"
fi

PASS=0; FAIL=0
declare -a FAILED_NAMES

if [[ -n "$ONLY_SCENARIO" ]]; then
  idx=$((ONLY_SCENARIO - 1))
  if (( idx < 0 || idx >= NUM_SCENARIOS )); then
    echo "${C_RED}✗ Invalid scenario number: $ONLY_SCENARIO${C_RESET}"
    echo "  Use --list to see available scenarios"
    exit 1
  fi
  if run_scenario "$idx"; then PASS=$((PASS+1)); else FAIL=$((FAIL+1)); FAILED_NAMES+=("${SCENARIOS_NAME[$idx]}"); fi
  # Cache hit test only runs when the user picks the cache_hit scenario.
  if [[ "${SCENARIOS_NAME[$idx]}" == "cache_hit" ]]; then
    run_cache_hit_test
  fi
else
  for i in $(seq 0 $((NUM_SCENARIOS - 1))); do
    # The cache_hit scenario is exercised by run_cache_hit_test below
    # (which fires it twice). Skip the single-shot run here to avoid
    # duplicate output.
    if [[ "${SCENARIOS_NAME[$i]}" == "cache_hit" ]]; then
      continue
    fi
    if run_scenario "$i"; then
      PASS=$((PASS+1))
    else
      FAIL=$((FAIL+1))
      FAILED_NAMES+=("${SCENARIOS_NAME[$i]}")
    fi
  done
  run_cache_hit_test
fi

# ── Summary ───────────────────────────────────────────────────────────────────
banner "SUMMARY" "$C_PURPLE"
printf "  ${C_GREEN}Passed: %d${C_RESET}    ${C_RED}Failed: %d${C_RESET}    Total: %d\n" \
  "$PASS" "$FAIL" "$((PASS+FAIL))"

if (( FAIL > 0 )); then
  printf "\n  ${C_RED}Failed scenarios:${C_RESET}\n"
  for n in "${FAILED_NAMES[@]}"; do
    printf "    - %s\n" "$n"
  done
  exit 1
fi
exit 0
