#!/usr/bin/env bash
# FIX129 C — TEST STACK ONLY: make a transaction stick, watch it heal and alert.
#
# Opens a psql session on the test gateway DB that takes the seed owner's
# portal_users row FOR UPDATE and then sits idle in transaction (stdin held
# open), plus a second session that tries the same lock (a waiter). Then
# polls, every 10 s for POLL_S seconds: /ready (status + pg_activity),
# pg_stat_activity, alert_events. Expected on the new code:
#   ~5-15 s   /ready reports idle_in_transaction=1, lock_waiters=1
#   30 s      the waiter fails: 55P03 lock_not_available (server lock_timeout)
#   60 s      /ready 503 failures=[stuck_transaction]; alert_events row for
#             stuck_transaction; ntfy message on the TEST topic;
#             backend log pg_stuck_transaction
#   120 s     the idle session is terminated by the server
#             (idle_in_transaction_session_timeout) and /ready is green again
# Refuses to run against anything but the sran-test project.
set -uo pipefail
export MSYS_NO_PATHCONV=1
PG=sran-test-postgres-1
BACKEND=http://127.0.0.1:18081
OUT="${1:?results dir}"
POLL_S="${POLL_S:-150}"
USER_ID=f1280000-0000-4000-8000-000000000011
mkdir -p "$OUT"
proj=$(docker inspect "$PG" --format '{{index .Config.Labels "com.docker.compose.project"}}')
[ "$proj" = "sran-test" ] || { echo "REFUSED: $PG belongs to project '$proj'"; exit 2; }

log() { echo "$(date -u +%H:%M:%S) $*" | tee -a "$OUT/timeline.txt"; }
psql_gw() { docker exec "$PG" psql -U postgres -d ai_gateway -At -c "$1"; }

start_ts=$(date -u +%s)
log "T+0 open idle-in-transaction holder (FOR UPDATE on $USER_ID) and a waiter"
docker exec -d "$PG" sh -c "(echo \"BEGIN; SELECT id FROM portal_users WHERE id='$USER_ID' FOR UPDATE;\"; sleep 300) | psql -U postgres -d ai_gateway -q"
sleep 2
docker exec -d "$PG" sh -c "psql -U postgres -d ai_gateway -At -c \"SELECT clock_timestamp(); SELECT id FROM portal_users WHERE id='$USER_ID' FOR UPDATE; SELECT clock_timestamp();\" > /tmp/fix129_waiter.txt 2>&1"

while :; do
  now=$(date -u +%s); t=$((now - start_ts))
  ready=$(curl -s -m 8 -o "$OUT/ready_$t.json" -w '%{http_code}' "$BACKEND/ready")
  pa=$(python -c "import json;d=json.load(open('$OUT/ready_$t.json'));p=d['checks'].get('pg_activity',{});print(d['status'],d['failures'],'idle=%s idle_age=%s waiters=%s stuck=%s'%(p.get('idle_in_transaction'),p.get('idle_in_transaction_max_age_s'),p.get('lock_waiters'),p.get('stuck_age_s')))" 2>/dev/null)
  act=$(psql_gw "select count(*) filter (where state='idle in transaction'), count(*) filter (where wait_event_type='Lock') from pg_stat_activity where datname='ai_gateway' and backend_type='client backend'")
  ev=$(psql_gw "select count(*) from alert_events where alert_type='stuck_transaction' and fired_at > now() - interval '10 minutes'")
  log "T+${t}s ready=$ready $pa | pg(idle,waiters)=$act | alert_events(stuck)=$ev"
  [ "$t" -ge "$POLL_S" ] && break
  sleep 10
done

log "waiter outcome:"; docker exec "$PG" cat /tmp/fix129_waiter.txt | tee -a "$OUT/timeline.txt"
log "postgres log (timeouts):"; docker logs --since 6m "$PG" 2>&1 | grep -iE "idle-in-transaction|lock timeout|canceling statement" | tail -5 | tee -a "$OUT/timeline.txt"
log "backend log:"; docker logs --since 6m sran-test-backend-1 2>&1 | grep -E "pg_stuck_transaction|alert_emitted|alert_delivered|credit_deduction" | tail -8 | tee -a "$OUT/timeline.txt"
psql_gw "select alert_type, severity, delivery_status, left(message,140), fired_at from alert_events where fired_at > now() - interval '10 minutes' order by fired_at" | tee -a "$OUT/alert_events.txt"
docker exec "$PG" sh -c 'pkill -f "sleep 300" ; true' >/dev/null 2>&1
log "done"
