#!/usr/bin/env bash
# FIX129 A5 — send one alert. Both channels are optional; each is used when
# its variables are set. The MESSAGE must never contain a secret — callers
# pass sizes, durations and check names only.
#
#   notify.sh <level> <title> <message>
#     level   high | ok | info       (ntfy priority + tag)
#     title   ASCII only (an HTTP header)
#
#   ALERT_NTFY_URL                 full topic URL, e.g. https://ntfy.sh/<topic>
#   RESEND_API_KEY + ALERT_EMAIL_TO  e-mail via https://api.resend.com/emails
#   ALERT_EMAIL_FROM               default no-reply@sran.ai
#
# Exit 0 when at least one configured channel accepted the message or when
# no channel is configured (logged); 1 when every configured channel failed.
set -uo pipefail
. /lib.sh

level="${1:?usage: notify.sh <high|ok|info> <title> <message>}"
title="${2:?title}"
message="${3:?message}"

case "$level" in
  high|error|fail) prio=high;    tags=rotating_light ;;
  ok|success)      prio=low;     tags=white_check_mark ;;
  *)               prio=default; tags=information_source ;;
esac

configured=0 sent=0

if [ -n "${ALERT_NTFY_URL:-}" ]; then
  configured=1
  if curl -fsS --max-time 20 -o /dev/null \
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
  if curl -fsS --max-time 20 -o /dev/null -X POST https://api.resend.com/emails \
       -H "Authorization: Bearer $RESEND_API_KEY" -H "Content-Type: application/json" \
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
