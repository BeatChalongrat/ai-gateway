#!/usr/bin/env bash
#
# Live monitoring helper for the load-test baseline. Polls docker stats and
# the gateway/backend Prometheus endpoints on an interval and prints a compact
# one-line-per-tick dashboard of the signals that matter during a stream load
# test.
#
# Metric names are taken from the RUNNING stack (verified):
#   backend  :8081/metrics        db_pool_active, db_pool_overflow, db_pool_max,
#                                  db_pool_checkout_timeouts_total,
#                                  http_requests_in_flight{route},
#                                  rate_limit_fail_open_total,
#                                  sidecar_fallback_total
#   gateway  :8080/__gw/metrics    gateway_in_flight_requests,
#                                  gateway_requests_total{path_group,status}
#
# Usage:
#   ./monitor.sh                # 5s interval, default endpoints
#   INTERVAL=2 ./monitor.sh     # 2s interval
#
# Stop with Ctrl-C. Run it in a second terminal next to the load test.
set -u

INTERVAL="${INTERVAL:-5}"
BACKEND_METRICS="${BACKEND_METRICS_URL:-http://localhost:8081/metrics}"
GATEWAY_METRICS="${GATEWAY_METRICS_URL:-http://localhost:8080/__gw/metrics}"
CONTAINERS="${MONITOR_CONTAINERS:-ai-gateway-backend-1 ai-gateway-gateway-1 ai-gateway-ner-service-1 ai-gateway-postgres-1 ai-gateway-redis-1 ai-gateway-rate-limiter-1 ai-gateway-dlp-sidecar-1}"

metric() {  # metric() <url> <prometheus-line-prefix> ; sums all matching samples
  curl -s "$1" 2>/dev/null | awk -v p="$2" '
    $0 ~ "^"p {
      v=$NF; if (v ~ /^[0-9.eE+-]+$/) { s+=v; n++ }
    } END { if (n>0) printf "%.0f", s; else printf "NA" }'
}

echo "monitor: interval=${INTERVAL}s  backend=${BACKEND_METRICS}  gateway=${GATEWAY_METRICS}"
echo "Columns: time | gw_inflight | be_inflight | dbpool act/ovf/max | db_timeouts | rl_failopen | sidecar_fb | v1_429 | v1_5xx"
echo "-------------------------------------------------------------------------------------------------------------------"

prev_429=0; prev_5xx=0; first=1
while true; do
  ts="$(date +%H:%M:%S 2>/dev/null || echo now)"

  gw_inflight="$(metric "$GATEWAY_METRICS" 'gateway_in_flight_requests')"
  be_inflight="$(curl -s "$BACKEND_METRICS" 2>/dev/null | awk '
    /^http_requests_in_flight\{/ { s+=$NF } END { printf "%.0f", s }')"
  db_act="$(metric "$BACKEND_METRICS" 'db_pool_active ')"
  db_ovf="$(metric "$BACKEND_METRICS" 'db_pool_overflow ')"
  db_max="$(metric "$BACKEND_METRICS" 'db_pool_max ')"
  db_to="$(metric "$BACKEND_METRICS" 'db_pool_checkout_timeouts_total ')"
  rl_fo="$(metric "$BACKEND_METRICS" 'rate_limit_fail_open_total ')"
  sc_fb="$(curl -s "$BACKEND_METRICS" 2>/dev/null | awk '
    /^sidecar_fallback_total\{/ { s+=$NF } END { printf "%.0f", (s==""?0:s) }')"

  # 429 / 5xx for the /v1 chat path (cumulative counters -> delta per tick).
  cur_429="$(curl -s "$GATEWAY_METRICS" 2>/dev/null | awk '
    /^gateway_requests_total\{.*path_group="\/v1".*status="429"/ { s+=$NF } END { printf "%.0f",(s==""?0:s) }')"
  cur_5xx="$(curl -s "$GATEWAY_METRICS" 2>/dev/null | awk '
    /^gateway_requests_total\{.*path_group="\/v1".*status="5/ { s+=$NF } END { printf "%.0f",(s==""?0:s) }')"
  if [[ ${first} -eq 1 ]]; then d429=0; d5xx=0; first=0; else
    d429=$(( cur_429 - prev_429 )); d5xx=$(( cur_5xx - prev_5xx )); fi
  prev_429=${cur_429}; prev_5xx=${cur_5xx}

  printf "%s | gw=%4s | be=%4s | db=%s/%s/%s | dbTO=%s | failopen=%s | sidecarFB=%s | 429+%s | 5xx+%s\n" \
    "${ts}" "${gw_inflight}" "${be_inflight}" "${db_act}" "${db_ovf}" "${db_max}" \
    "${db_to}" "${rl_fo}" "${sc_fb}" "${d429}" "${d5xx}"

  sleep "${INTERVAL}"
done
