#!/usr/bin/env bash
# FIX129 A5 — send one alert. Both channels are optional; each is used when
# its variables are set. The MESSAGE must never contain a secret — callers
# pass sizes, durations and check names only; and since FIX130 E4 the title
# and message go through lib.sh `redact` here, the last hop, so a caller that
# slips (a DSN, a token, a SQL line in an error) cannot leak it.
#
#   notify.sh <level> <title> <message>
#     level   high | ok | info       (ntfy priority + tag)
#     title   ASCII only (an HTTP header)
#
#   ALERT_NTFY_URL                 full topic URL, e.g. https://ntfy.sh/<topic>
#   ALERT_NTFY_TOKEN               optional ntfy access token (tk_...) for a
#                                  reserved / ACL-protected or self-hosted
#                                  topic: "Authorization: Bearer <token>"
#   RESEND_API_KEY + ALERT_EMAIL_TO  e-mail via https://api.resend.com/emails
#   ALERT_EMAIL_FROM               default no-reply@sran.ai
#   RESEND_API_URL                 default https://api.resend.com/emails (tests
#                                  point it at a local dummy endpoint)
#
# FIX131 — a test alert can never reach the owner:
#   ALERT_ENVIRONMENT              empty / production / live = the real system.
#                                  Anything else labels title AND message
#                                  ("[TEST] ", "[STAGING] " ...); test / testing
#                                  / ci / lane / test-stack also ROUTE the alert
#                                  to ALERT_TEST_NTFY_URL only (no e-mail, never
#                                  ALERT_NTFY_URL, never its token).
#   NOTIFY_TEST=1                  a test fire (`entrypoint.sh notify ...`): the
#                                  same as ALERT_ENVIRONMENT=test for this call.
#   ALERT_TEST_NTFY_URL            the throwaway topic for test alerts; empty =
#                                  test alerts are logged only.
#
# Exit 0 when at least one configured channel accepted the message or when
# no channel is configured (logged); 1 when every configured channel failed.
set -uo pipefail
. /lib.sh

level="${1:?usage: notify.sh <high|ok|info> <title> <message>}"
title="$(printf '%s' "${2:?title}" | redact)"
message="$(printf '%s' "${3:?message}" | redact)"

# FIX131 — the environment label and the test route (see the header).
env_name=$(printf '%s' "${ALERT_ENVIRONMENT:-}" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')
label=""
test_route=0
case "$env_name" in
  ""|prod|production|live) ;;
  test|testing|ci|lane|test-stack) label="[TEST]"; test_route=1 ;;
  *) label="[$(printf '%s' "$env_name" | tr '[:lower:]' '[:upper:]')]" ;;
esac
if [ "${NOTIFY_TEST:-0}" = "1" ]; then label="[TEST]"; test_route=1; fi
if [ -n "$label" ]; then
  title="$label $title"
  message="$label $message"
fi

# Secret headers ride a curl config on stdin, never argv (argv shows in ps).
# curl_bearer <token> <curl args...>: with a token, the Authorization header
# comes from a curl config read on stdin; without one, no header at all.
curl_bearer() {
  local token="$1"; shift
  if [ -n "$token" ]; then
    printf 'header = "Authorization: Bearer %s"\n' "$token" | curl --config - "$@"
  else
    curl "$@" </dev/null
  fi
}

case "$level" in
  high|error|fail) prio=high;    tags=rotating_light ;;
  ok|success)      prio=low;     tags=white_check_mark ;;
  *)               prio=default; tags=information_source ;;
esac

configured=0 sent=0

if [ "$test_route" = 1 ]; then
  # A test alert: the throwaway test topic or nowhere. Never the owner's
  # topic, never its token, never e-mail.
  if [ -z "${ALERT_TEST_NTFY_URL:-}" ]; then
    log "notify: test alert, no ALERT_TEST_NTFY_URL - logged only: $title - $message"
    exit 0
  fi
  if curl -fsS --max-time 20 -o /dev/null \
       -H "Title: $title" -H "Priority: $prio" -H "Tags: $tags,test" \
       --data-raw "$message" "$ALERT_TEST_NTFY_URL" </dev/null; then
    log "notify: test topic accepted ($prio)"
    exit 0
  fi
  log "notify: WARN test topic POST failed"
  exit 1
fi

if [ -n "${ALERT_NTFY_URL:-}" ]; then
  configured=1
  if curl_bearer "${ALERT_NTFY_TOKEN:-}" -fsS --max-time 20 -o /dev/null \
       -H "Title: $title" -H "Priority: $prio" -H "Tags: $tags" \
       --data-raw "$message" "$ALERT_NTFY_URL"; then
    sent=1; log "notify: ntfy accepted ($prio)"
  else
    log "notify: WARN ntfy POST failed"
  fi
fi

if [ -n "${RESEND_API_KEY:-}" ] && [ -n "${ALERT_EMAIL_TO:-}" ]; then
  configured=1
  payload=$(jq -n --arg from "${ALERT_EMAIL_FROM:-no-reply@sran.ai}" --arg to "$ALERT_EMAIL_TO" \
                  --arg subject "[$level] $title" --arg text "$message" \
                  '{from: $from, to: ($to | split(",") | map(gsub("^\\s+|\\s+$"; ""))), subject: $subject, text: $text}')
  if curl_bearer "$RESEND_API_KEY" -fsS --max-time 20 -o /dev/null -X POST "${RESEND_API_URL:-https://api.resend.com/emails}" \
       -H "Content-Type: application/json" \
       --data-raw "$payload"; then
    sent=1; log "notify: resend accepted (to $ALERT_EMAIL_TO)"
  else
    log "notify: WARN resend POST failed"
  fi
fi

if [ $configured -eq 0 ]; then
  log "notify: no alert channel configured (set ALERT_NTFY_URL and/or RESEND_API_KEY + ALERT_EMAIL_TO); message was: $title - $message"
  exit 0
fi
[ $sent -eq 1 ]
