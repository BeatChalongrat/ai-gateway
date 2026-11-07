#!/usr/bin/env bash
#
# Staged growth baseline for the SRAN AI Gateway streaming chat path.
#
# Runs 25 -> 50 -> 100 -> 150 -> 250 concurrent virtual users, each stage
# gated on the previous one PASSING. Stops immediately on the first failing
# stage (the load-test script exits non-zero when success-rate / 429 / 5xx
# breach thresholds).
#
# Durations are configurable so the full suite can be shortened in dev.
# Defaults match the task spec (5/10/10/10/12 min). Override with env vars:
#
#   STAGE_DURATIONS="60 120 120 120 150"   # seconds per stage (dev: short)
#   MAX_CONCURRENCY=150                     # cap the suite (skip 250)
#
# Required env:
#   GATEWAY_API_KEY            shared gateway key (Bearer)
# Recommended env:
#   INTERNAL_SERVICE_SECRET    enables trusted per-user X-User-ID keying
#
# Usage:
#   GATEWAY_API_KEY=gw_xxx INTERNAL_SERVICE_SECRET=sek \
#     ./run_growth_baseline.sh gpt-4o-mini
#
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${LOADTEST_PYTHON:-python}"
LOADTEST="${SCRIPT_DIR}/stream_chat_loadtest.py"

MODEL="${1:-gpt-4o-mini}"
URL="${LOADTEST_URL:-http://127.0.0.1:28080/v1/chat/completions}"
API_KEY="${GATEWAY_API_KEY:-}"
SECRET="${INTERNAL_SERVICE_SECRET:-}"
PAYLOAD_KB="${PAYLOAD_SIZE_KB:-2}"
NER_MODE="${NER_MODE:-default}"
RAMP="${RAMP_SECONDS:-30}"
THINK="${THINK_TIME_SECONDS:-1.0}"
JITTER="${JITTER_SECONDS:-1.0}"
MAX_CONCURRENCY="${MAX_CONCURRENCY:-250}"
OUT_DIR="${OUTPUT_DIR:-${SCRIPT_DIR}/results}"

# Per-stage thresholds (override to relax for an exploratory run).
MIN_SUCCESS="${MIN_SUCCESS_RATE:-0.99}"
MAX_429="${MAX_429_RATE:-0.01}"
MAX_5XX="${MAX_5XX_RATE:-0.005}"

# Stage concurrencies + default durations (seconds): 5/10/10/10/12 min.
STAGES_CONCURRENCY=(25 50 100 150 250)
DEFAULT_DURATIONS=(300 600 600 600 720)
read -r -a DURATIONS <<< "${STAGE_DURATIONS:-${DEFAULT_DURATIONS[*]}}"

if [[ -z "${API_KEY}" ]]; then
  echo "ERROR: GATEWAY_API_KEY is required." >&2
  exit 2
fi
if [[ ! -f "${LOADTEST}" ]]; then
  echo "ERROR: cannot find ${LOADTEST}" >&2
  exit 2
fi
mkdir -p "${OUT_DIR}"

echo "============================================================"
echo " SRAN Gateway growth baseline"
echo "   url           : ${URL}"
echo "   model         : ${MODEL}"
echo "   payload       : ~${PAYLOAD_KB} KB    ner_mode=${NER_MODE}"
echo "   trusted keying: $([[ -n "${SECRET}" ]] && echo yes || echo NO)"
echo "   stages        : ${STAGES_CONCURRENCY[*]}  (cap ${MAX_CONCURRENCY})"
echo "   durations(s)  : ${DURATIONS[*]}"
echo "   output        : ${OUT_DIR}"
echo "============================================================"

ts="$(date +%Y%m%d_%H%M%S 2>/dev/null || echo run)"
overall_rc=0

for i in "${!STAGES_CONCURRENCY[@]}"; do
  conc="${STAGES_CONCURRENCY[$i]}"
  dur="${DURATIONS[$i]:-300}"

  if (( conc > MAX_CONCURRENCY )); then
    echo ">>> Skipping stage ${conc} (above MAX_CONCURRENCY=${MAX_CONCURRENCY})"
    continue
  fi

  out_json="${OUT_DIR}/${ts}_stage_${conc}.json"
  echo ""
  echo ">>> STAGE: ${conc} concurrent for ${dur}s  ->  ${out_json}"
  echo ""

  "${PY}" "${LOADTEST}" \
    --url "${URL}" \
    --api-key "${API_KEY}" \
    --internal-service-secret "${SECRET}" \
    --model "${MODEL}" \
    --concurrency "${conc}" \
    --duration-seconds "${dur}" \
    --ramp-seconds "${RAMP}" \
    --payload-size-kb "${PAYLOAD_KB}" \
    --ner-mode "${NER_MODE}" \
    --think-time-seconds "${THINK}" \
    --jitter-seconds "${JITTER}" \
    --stream true \
    --min-success-rate "${MIN_SUCCESS}" \
    --max-429-rate "${MAX_429}" \
    --max-5xx-rate "${MAX_5XX}" \
    --output-json "${out_json}"
  rc=$?

  if [[ ${rc} -ne 0 ]]; then
    echo ""
    echo "############################################################"
    echo "# STAGE ${conc} FAILED (exit ${rc}). Stopping the suite."
    echo "# Inspect ${out_json} and the monitoring helper output."
    echo "############################################################"
    overall_rc=${rc}
    break
  fi
  echo ">>> STAGE ${conc} PASSED."
  # Brief cooldown so rate-limit windows + pools drain between stages.
  sleep "${STAGE_COOLDOWN_SECONDS:-15}"
done

echo ""
if [[ ${overall_rc} -eq 0 ]]; then
  echo "ALL ATTEMPTED STAGES PASSED (cap ${MAX_CONCURRENCY})."
else
  echo "SUITE FAILED at exit ${overall_rc}."
fi
exit ${overall_rc}
