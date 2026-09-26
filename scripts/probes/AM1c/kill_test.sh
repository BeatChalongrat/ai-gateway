#!/usr/bin/env bash
# AM1c — (re)create the TEST stack's agent runner for kill_test.js: image
# ai-gateway-agent-runner:dev, 30 s step wall, a sweep every 10 s with 10 s
# grace. The test stack's own runner key (never the live one: the shell value
# wins over .env).
#
#   bash scripts/probes/AM1c/kill_test.sh up|recreate|down
set -e
ROOT=$(git rev-parse --show-toplevel)
cd "$ROOT"
export AM_NETWORK=sran-test_default AM_UPLOADS_VOLUME=sran-test_ts_chat_edge_uploads
export AM_CONTAINER_NAME=sran-test-agent-runner AM_RUNNER_PORT=18095 AM_RUNNER_TAG=dev
export AM_SANDBOX_HOST_DIR="$(cygpath -m "$ROOT")/data/agent-sandbox-runs-test"
export AGENT_SANDBOX_WALL_SECONDS=30 AGENT_RUNNER_SWEEP_SECONDS=10 AGENT_RUNNER_SWEEP_GRACE_SECONDS=10
AGENT_RUNNER_GATEWAY_API_KEY="$(cat scripts/probes/AM1/results/runner_key.test)"
export AGENT_RUNNER_GATEWAY_API_KEY
DC=(docker compose --env-file .env -p sran-agent-market-test -f docker-compose.agent-market.yml --profile agent-market)
case "${1:-up}" in
  up|recreate) timeout 300 "${DC[@]}" up -d --force-recreate --wait agent-runner 2>&1 | tail -1 ;;
  down) "${DC[@]}" down 2>&1 | tail -1 ;;
esac
