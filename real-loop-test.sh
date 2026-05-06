#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# SRAN AI Gateway — Real loop test (live OpenAI)
#
# Verifies the full map → AI → unmap loop end-to-end against a live gateway
# routing to a real OpenAI provider key. NO stubs, NO mocks.
#
# Three scenarios:
#   A (mask)  — Thai message containing @example.com + 081-XXX-XXXX
#               Expected: HTTP 200, policy_action=mask, restore brings back
#                          the email + phone in the user-visible reply.
#   B (block) — English message containing "Bearer sk-proj-..."
#               Expected: HTTP 403, error.code=dlp_violation.
#   C (allow) — "What is 2 plus 2?"
#               Expected: HTTP 200, policy_action=allow, model returns "4".
#
# Usage:
#   export GATEWAY_KEY=gw_...
#   ./real-loop-test.sh             # run all 3
#   ./real-loop-test.sh --list      # list scenarios (no network)
#   ./real-loop-test.sh --help      # this help
# ─────────────────────────────────────────────────────────────────────────────

set -uo pipefail

if (( BASH_VERSINFO[0] < 4 )); then
  echo "Error: bash 4+ required (found $BASH_VERSION)" >&2
  exit 1
fi

GATEWAY_URL="${GATEWAY_URL:-http://localhost:8080}"
GATEWAY_KEY="${GATEWAY_KEY:-}"
MODEL="${MODEL:-gpt-4o-mini}"

if [[ -t 1 ]]; then
  C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'; C_DIM=$'\033[2m'
  C_RED=$'\033[31m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'
  C_CYAN=$'\033[36m'; C_MAGENTA=$'\033[35m'
else
  C_RESET=""; C_BOLD=""; C_DIM=""
  C_RED=""; C_GREEN=""; C_YELLOW=""; C_CYAN=""; C_MAGENTA=""
fi

# ── Scenario inputs ──────────────────────────────────────────────────────────
# Thai input that contains:
#   - email matching @example.com (so Phase 3 query "input_raw LIKE %@example.com%" hits)
#   - Thai mobile phone in 081-XXX-XXXX format (PhoneDetector should pick this up)
SCENARIO_A_INPUT='ส่งใบเสร็จไปที่ alice.smith@example.com แล้วโทรหาฉันที่ 081-234-5678 ด้วยนะ'

# English input with a fake-but-realistic OpenAI session-key prefix.
# api_key_secret detector should classify as CRITICAL → block under DEFAULT_PROFILE.
SCENARIO_B_INPUT='Please validate this token for me: Bearer sk-proj-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1234567890ABCDEFghijklmn'

# Plain-vanilla math question, no PII anywhere.
SCENARIO_C_INPUT='What is 2 plus 2?'

# ── Helpers ──────────────────────────────────────────────────────────────────
print_help() {
  cat <<EOF
${C_BOLD}real-loop-test.sh${C_RESET} — verify map → AI → unmap end-to-end

Environment:
  GATEWAY_URL    Gateway base URL  (default: http://localhost:8080)
  GATEWAY_KEY    Plaintext gateway key (REQUIRED, must start with gw_)
  MODEL          Chat model        (default: gpt-4o-mini)

Usage:
  export GATEWAY_KEY=gw_...
  ./real-loop-test.sh              run all 3 scenarios
  ./real-loop-test.sh --list       print scenario plan, no network
  ./real-loop-test.sh --help       this help

Scenarios:
  A (mask)   Thai email + phone   → expect 200 / policy_action=mask / restored
  B (block)  Bearer sk-proj- key  → expect 403 / dlp_violation
  C (allow)  '2 plus 2'           → expect 200 / policy_action=allow / "4"

Exit codes:
  0  — all 3 scenarios returned the expected HTTP status
  1  — any scenario diverged from expectation OR pre-flight failed
EOF
}

print_list() {
  printf "${C_BOLD}Real loop scenarios${C_RESET}\n"
  printf "  %-3s %-7s %-50s %s\n" "#"  "PATH"   "INPUT (truncated)" "EXPECT"
  printf "  %-3s %-7s %-50s %s\n" "A"  "mask"   "${SCENARIO_A_INPUT:0:48}..." "200 mask"
  printf "  %-3s %-7s %-50s %s\n" "B"  "block"  "${SCENARIO_B_INPUT:0:48}..." "403 dlp_violation"
  printf "  %-3s %-7s %-50s %s\n" "C"  "allow"  "${SCENARIO_C_INPUT:0:48}..." "200 allow"
}

err() { printf "${C_RED}✗ %s${C_RESET}\n" "$*" >&2; }
ok()  { printf "${C_GREEN}✓ %s${C_RESET}\n" "$*"; }
info(){ printf "${C_CYAN}→ %s${C_RESET}\n" "$*"; }
hr()  { printf "${C_DIM}%s${C_RESET}\n" "─────────────────────────────────────────────────────────────"; }

# Build a chat-completions payload with jq -n. Single user-role message.
# Writes to the path given as $2 — never via $(...) capture, which on
# Windows MSYS bash collapses non-ASCII bytes (e.g. Thai UTF-8) to '?'.
# See docs/known-issues/dlp-detector-gaps.md issue #8.
build_payload() {
  local content="$1"
  local out_file="$2"
  jq -n \
    --arg model "$MODEL" \
    --arg content "$content" \
    '{
       model: $model,
       messages: [{role: "user", content: $content}],
       metadata: {}
     }' > "$out_file"
}

# Run one scenario.  Args:
#   1: label (A/B/C)
#   2: path  (mask/block/allow — display only)
#   3: input text
#   4: expected HTTP status (200 / 403)
# Returns 0 if response matches expected status, else 1.
run_scenario() {
  local label="$1" path_label="$2" input="$3" expect_status="$4"
  hr
  printf "${C_BOLD}Scenario %s — %s${C_RESET}\n" "$label" "$path_label"
  printf "${C_DIM}USER INPUT:${C_RESET} %s\n" "$input"

  local payload_file body_file headers_file rc t_start t_end latency_ms
  payload_file=$(mktemp --suffix=.json)
  body_file=$(mktemp)
  headers_file=$(mktemp)
  build_payload "$input" "$payload_file"
  t_start=$(date +%s%3N)

  http_status=$(curl -sS -o "$body_file" -D "$headers_file" -w "%{http_code}" \
    -X POST "${GATEWAY_URL}/v1/chat/completions" \
    -H "Authorization: Bearer ${GATEWAY_KEY}" \
    -H "Content-Type: application/json" \
    --data-binary @"$payload_file" 2>&1)
  rc=$?

  t_end=$(date +%s%3N)
  latency_ms=$(( t_end - t_start ))

  if (( rc != 0 )); then
    err "curl failed (rc=$rc): $http_status"
    rm -f "$body_file" "$headers_file" "$payload_file"
    return 1
  fi

  printf "${C_DIM}HTTP STATUS:${C_RESET} %s   ${C_DIM}LATENCY:${C_RESET} %sms\n" "$http_status" "$latency_ms"

  local body
  body=$(cat "$body_file")
  rm -f "$body_file" "$headers_file" "$payload_file"

  # Try to parse as JSON; if it isn't, dump raw and bail.
  if ! echo "$body" | jq -e . >/dev/null 2>&1; then
    err "response was not valid JSON:"
    echo "$body" | head -20
    return 1
  fi

  if [[ "$http_status" == "200" ]]; then
    local request_id reply policy_action
    request_id=$(echo "$body" | jq -r '.id // "<no id>"')
    reply=$(echo "$body" | jq -r '.choices[0].message.content // "<no content>"')
    policy_action=$(echo "$body" | jq -r '.policy_action // "<missing>"')

    printf "${C_DIM}REQUEST ID:${C_RESET}  %s\n" "$request_id"
    printf "${C_DIM}POLICY:${C_RESET}      %s\n" "$policy_action"
    printf "${C_MAGENTA}AI REPLY (after restore):${C_RESET}\n  %s\n" "$reply"
  else
    local err_code err_msg
    err_code=$(echo "$body" | jq -r '.error.code // "<no code>"')
    err_msg=$(echo "$body" | jq -r '.error.message // .detail // "<no message>"')
    printf "${C_DIM}ERROR CODE:${C_RESET}  %s\n" "$err_code"
    printf "${C_DIM}ERROR MSG:${C_RESET}   %s\n" "$err_msg"
  fi

  if [[ "$http_status" == "$expect_status" ]]; then
    ok "Scenario $label hit expected HTTP $expect_status"
    return 0
  else
    err "Scenario $label expected HTTP $expect_status, got $http_status"
    return 1
  fi
}

# ── Arg parsing ──────────────────────────────────────────────────────────────
case "${1:-}" in
  -h|--help)  print_help; exit 0 ;;
  --list)     print_list; exit 0 ;;
  "")         ;;  # default: run all
  *)          err "unknown arg: $1"; print_help; exit 1 ;;
esac

# ── Pre-flight ───────────────────────────────────────────────────────────────
if [[ -z "$GATEWAY_KEY" ]]; then
  err "GATEWAY_KEY not set — export your gw_ key first:"
  printf "    ${C_DIM}export GATEWAY_KEY=gw_...${C_RESET}\n" >&2
  exit 1
fi

if [[ "$GATEWAY_KEY" != gw_* ]]; then
  err "GATEWAY_KEY does not start with 'gw_' — got '${GATEWAY_KEY:0:8}...'"
  exit 1
fi

info "Pre-flight: gateway health"
if ! curl -fsS "${GATEWAY_URL}/health" >/dev/null 2>&1; then
  err "gateway health check failed: ${GATEWAY_URL}/health is not responding"
  exit 1
fi
ok "Gateway up at ${GATEWAY_URL}"

# ── Run scenarios ────────────────────────────────────────────────────────────
fail_count=0

run_scenario "A" "mask"  "$SCENARIO_A_INPUT" "200" || fail_count=$(( fail_count + 1 ))
sleep 2
run_scenario "B" "block" "$SCENARIO_B_INPUT" "403" || fail_count=$(( fail_count + 1 ))
sleep 2
run_scenario "C" "allow" "$SCENARIO_C_INPUT" "200" || fail_count=$(( fail_count + 1 ))

hr
if (( fail_count == 0 )); then
  ok "All 3 scenarios returned their expected HTTP status."
  printf "${C_DIM}Run the Phase 3 DB queries to verify map/restore content.${C_RESET}\n"
  exit 0
else
  err "$fail_count of 3 scenarios diverged from expected status."
  exit 1
fi
