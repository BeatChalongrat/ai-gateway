#!/usr/bin/env bash
# The gate, one heavy thing at a time.
#
# This host is a 32 GB laptop that also runs live. A gate used to start a lane, the test stack, image
# builds and a desktop build as each came to mind; together they took the host to 1.1 GB free five times
# in one day, the integration lane was cut twice, and nothing had a verdict. Run alone, the integration
# lane takes 41 minutes and passes.
#
# So a gate is this script and nothing beside it. Every phase:
#   * takes the lock — a second heavy phase does not start while one runs;
#   * waits for the host to have GATE_MIN_FREE_MB free (default 3000), and gives up saying so;
#   * stops what the phase does not need (a lane runs with the test stack stopped);
#   * writes its output to GATE_RESULTS/<phase>.txt and prints one verdict line.
# Before anything, live's own memory settings are checked and put right (scripts/ops/live_memory.py):
# every live container keeps its floor and its turn, so what a gate starts is the kernel's first choice.
#
#   bash scripts/gate.sh preflight            live's memory guard, free memory, what is running
#   bash scripts/gate.sh lanes                engine · go units, vet, format · backend · web · admin · desktop
#   bash scripts/gate.sh it                   the integration lane, alone, each chunk's verdict as it ends
#   bash scripts/gate.sh build <sha>          the five images, then the VM's cache handed back
#   bash scripts/gate.sh stack <sha>          test stack + stand-in web + built clients (a walk needs them)
#   bash scripts/gate.sh stack-stop           stop the test stack (nothing is removed)
#   bash scripts/gate.sh walk -- <command…>   one walk, with the lock held
#   bash scripts/gate.sh smoke <sha>          smoke on the test stack: flag on, flag off, back on
#   bash scripts/gate.sh drill                removal drill + gitleaks on the unpushed commits
#   bash scripts/gate.sh backup               a live backup with its restore drill (never recreates a dependency)
#
# A card says what is its own:  GATE_RESULTS (where the files go) · GATE_BACKEND_TESTS (the backend
# lane's files) · GATE_IT (its integration lane, run after the AM10 and web chunks).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
RESULTS="${GATE_RESULTS:-scripts/probes/gate/results}"
MIN_FREE="${GATE_MIN_FREE_MB:-3000}"
WAIT_S="${GATE_WAIT_S:-600}"
BACKEND_TESTS="${GATE_BACKEND_TESTS:-tests/test_memory_guard_compose.py tests/test_am11b_instructions_echo.py tests/test_am12_prompt_echo.py tests/test_am1b_smoke_bulkhead.py tests/test_am1c_agent_market_pager.py tests/test_am7_admin_audit.py tests/test_am9_admin_accounts.py tests/test_am9_mail_connector.py tests/test_dlp_in_house.py tests/test_dlp_in_house_dispatcher.py}"
LOCK="${GATE_LOCK:-${TMP:-/tmp}/sran-gate.lock}"
mkdir -p "$RESULTS"

free_mb() { # the host's free memory: Windows' own figure, or the kernel's on a Linux host
  if [ -n "${GATE_FREE_MB:-}" ]; then echo "$GATE_FREE_MB"; return; fi # (the script's own test says what the host has)
  if command -v powershell > /dev/null 2>&1; then
    powershell -NoProfile -Command "[int]((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory/1024)" 2>/dev/null | tr -d '\r'
  else
    awk '/^MemAvailable:/ {print int($2/1024)}' /proc/meminfo 2>/dev/null
  fi
}
say() { printf '%s gate %s\n' "$(date -u +%H:%M:%SZ)" "$*"; }
test_containers() { docker ps --format '{{.Names}}' | grep -E '^sran-test-|^sran-agent-market-test-' || true; }

lock() {
  if ! mkdir "$LOCK" 2>/dev/null; then
    say "REFUSED: another heavy phase holds the lock ($(cat "$LOCK/what" 2>/dev/null || echo unknown)) — one at a time. Stale? rmdir after checking: $LOCK"
    exit 3
  fi
  echo "$1 since $(date -u +%H:%M:%SZ) pid $$" > "$LOCK/what"
  trap 'rm -rf "$LOCK"' EXIT
}
room() { # wait for the host to have room; give up saying how much it had
  local waited=0 f
  while :; do
    f=$(free_mb); f=${f:-0}
    [ "$f" -ge "$MIN_FREE" ] && { say "host has ${f} MB free (needs ${MIN_FREE})"; return 0; }
    [ "$waited" -ge "$WAIT_S" ] && { say "STOPPED: the host has ${f} MB free after ${WAIT_S} s, the phase needs ${MIN_FREE}. Close something or run later — nothing was started."; exit 4; }
    [ "$waited" = 0 ] && { say "host has ${f} MB free, the phase needs ${MIN_FREE} — handing the VM's cache back and waiting up to ${WAIT_S} s"; cache_back; }
    sleep 20; waited=$((waited + 20))
  done
}
guard() { # live keeps its floor and its turn before a gate takes anything
  if ! python scripts/ops/live_memory.py check > "$RESULTS/live_memory.txt" 2>&1; then
    say "live's memory settings differ from compose — putting them right (no restart)"
    python scripts/ops/live_memory.py apply > "$RESULTS/live_memory.txt" 2>&1 || { say "STOPPED: could not apply live's memory settings — see $RESULTS/live_memory.txt"; exit 5; }
  fi
  say "live memory guard: $(tail -1 "$RESULTS/live_memory.txt")"
}
stack_stop() {
  local t; t=$(test_containers)
  [ -n "$t" ] && docker stop $t > /dev/null 2>&1
  say "test stack stopped ($(test_containers | wc -l | tr -d ' ') still up)"
}
cache_back() { # hand the VM's free memory back to Windows
  # The VM gives a page back only when it is part of a free block of 2 MB, and with live running in it the
  # VM is never idle enough for WSL's own reclaim. So: drop the page cache, then have the kernel compact
  # what is free into such blocks (7 Oct 2026: 5.5 GB free inside the VM, 1.1 GB free on the host; one
  # compaction gave the host 1.4 GB in twenty seconds, a second and a third nothing more).
  # No sync: a sync in this VM once never returned, and its container could not be removed.
  timeout 120 docker run --rm --privileged --network none --oom-score-adj 1000 alpine:3 \
    sh -c 'echo 1 > /proc/sys/vm/drop_caches; echo 1 > /proc/sys/vm/compact_memory' > /dev/null 2>&1 || true
  sleep 20 # (the pages reach Windows a few seconds after)
}
one() { # one <name> <timeout s> <command…> → a file and a verdict line
  local name="$1" t="$2"; shift 2
  local out="$RESULTS/$name.txt" start=$SECONDS
  ( timeout "$t" "$@" > "$out" 2>&1; echo "exit=$?" >> "$out" )
  local rc; rc=$(grep -a '^exit=' "$out" | tail -1 | cut -d= -f2)
  say "$name: $([ "$rc" = "0" ] && echo ok || echo "FAILED (exit $rc)") in $((SECONDS - start)) s → $out"
  [ "$rc" = "0" ]
}

phase="${1:-}"; shift || true
case "$phase" in
  preflight)
    guard
    say "host free: $(free_mb) MB · test containers up: $(test_containers | wc -l | tr -d ' ') · lock: $([ -d "$LOCK" ] && cat "$LOCK/what" || echo free)"
    ;;
  lanes)
    lock lanes; stack_stop; room; guard; rc=0
    one python_lane 3000 bash scripts/agent-market/python-lane.sh || rc=1
    room; one go_units 2700 bash scripts/probes/AM7/go_test.sh ./... -count=1 || rc=1
    one go_vet 600 bash -c 'cd services/chat-edge-api && go vet ./...' || rc=1
    one gofmt 120 bash -c 'cd services/chat-edge-api && test -z "$(gofmt -l internal/agentmarket cmd/agent-runner)"' || rc=1
    # shellcheck disable=SC2086
    room; one backend_lane 900 bash scripts/backend-test-lane.sh $BACKEND_TESTS -q -p no:cacheprovider || rc=1
    room; one web_tsc 900 bash -c 'cd apps/web-chat && npx tsc --noEmit -p .' || rc=1
    one web_vitest 1500 bash -c 'cd apps/web-chat && npx vitest run' || rc=1
    one web_lints 600 bash -c 'cd apps/web-chat && npm run -s lint:i18n && npm run -s check:classes && npm run -s lint:theme' || rc=1
    room; one admin 900 bash -c 'cd admin-ui && npx tsc --noEmit -p . && npx vitest run' || rc=1
    one desktop 900 bash -c 'cd apps/desktop && npm run -s typecheck && npx vitest run' || rc=1
    say "lanes: $([ $rc = 0 ] && echo ALL GREEN || echo RED)"; exit $rc
    ;;
  it)
    lock it; stack_stop; room; guard
    export AGENT_MARKET_IT=1
    out="$RESULTS/it_lane.txt"; : > "$out"; rc=0
    {
      date -u +"started %H:%M:%SZ at $(git rev-parse --short=7 HEAD)"
      timeout 4200 bash scripts/probes/AM10/it_lane.sh | sed -u 's/^lane_exit=/am10_lane_exit=/'
      timeout 2400 bash scripts/probes/AM12/it_lane.sh web | sed -u 's/^lane_exit=/am12_web_lane_exit=/'
      [ -n "${GATE_IT:-scripts/probes/AM11b/it_lane.sh asked}" ] && timeout 1800 bash ${GATE_IT:-scripts/probes/AM11b/it_lane.sh asked} | sed -u 's/^lane_exit=/card_lane_exit=/'
      date -u +"ended %H:%M:%SZ"
    } >> "$out" 2>&1
    grep -a -q -E '^(--- FAIL|FAIL|panic)' "$out" && rc=1
    grep -a -E '_lane_exit=' "$out" | grep -v -q '=0$' && rc=1
    [ "$(grep -a -c -E '_lane_exit=0$' "$out")" -ge 3 ] || rc=1
    say "integration: $(grep -a -c '^--- PASS' "$out") passed, $(grep -a -c '^--- FAIL' "$out") failed · $(grep -a -E '_lane_exit=' "$out" | tr '\n' ' ')→ $out"
    exit $rc
    ;;
  build)
    sha="${1:?the commit to build (7 characters)}"
    lock build; stack_stop; room; guard; rc=0
    one build_images 2400 bash scripts/build-images.sh --rev "$sha" chat-edge-api web-chat admin-ui || rc=1
    room; one build_runner 1800 bash scripts/agent-market/build.sh "$sha" || rc=1
    one build_fetcher 1800 bash scripts/agent-market/build-fetcher.sh "$sha" || rc=1
    cache_back; say "build: $([ $rc = 0 ] && echo ok || echo FAILED) · host free now $(free_mb) MB"; exit $rc
    ;;
  stack)
    sha="${1:?the commit whose images the test stack runs}"
    lock stack; room; guard; rc=0
    one stack_up 1200 env AM7_NO_NER=1 TS_BACKEND_TAG="${TS_BACKEND_TAG:-2fa9bb8}" AM_FETCHER_TAG="${AM_FETCHER_TAG:-$sha}" bash scripts/probes/AM7/test_stack.sh up "$sha" || rc=1
    # (the stand-in sites run on the fetcher's image: the card's own when it built one, else the one named)
    one stack_web 600 bash scripts/probes/AM12/test_stack.sh web "${AM_FETCHER_TAG:-$sha}" || rc=1
    one clients_build 1500 bash scripts/probes/AM10/test_clients.sh build "$sha" || rc=1
    one clients_up 600 bash scripts/probes/AM10/test_clients.sh up "$sha" || rc=1
    cache_back; exit $rc
    ;;
  stack-stop) stack_stop ;;
  walk)
    [ "${1:-}" = "--" ] && shift
    lock "walk $*"; room; guard
    "$@"
    ;;
  smoke)
    sha="${1:?the commit the test stack runs}"
    lock smoke; guard; rc=0
    one smoke_test_on 600 env SMOKE_TARGET=test SMOKE_DESKTOP=0 bash scripts/smoke.sh || rc=1
    sleep 95 # (the gateway's rate limit: 90 s of quiet between two runs)
    bash scripts/probes/AM7/test_stack.sh edge "$sha" off > /dev/null 2>&1; docker stop sran-test-agent-runner > /dev/null 2>&1
    one smoke_test_off 600 env AGENT_MARKET_ENABLED=false SMOKE_TARGET=test SMOKE_DESKTOP=0 bash scripts/smoke.sh || rc=1
    bash scripts/probes/AM7/test_stack.sh edge "$sha" on > /dev/null 2>&1; AM7_NO_NER=1 bash scripts/probes/AM7/test_stack.sh runner "$sha" > /dev/null 2>&1
    exit $rc
    ;;
  drill)
    lock drill; rc=0
    one removal_drill 1700 bash scripts/probes/AM2/removal_drill.sh || rc=1
    one gitleaks 600 env MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W 2>/dev/null || pwd):/repo" ghcr.io/gitleaks/gitleaks:v8.30.1 git --config /repo/.gitleaks.toml --log-opts="gitlab/main..main" --no-banner --redact /repo || rc=1
    say "drill: $(grep -a 'diff reverted' "$RESULTS/removal_drill.txt" | tail -1) · gitleaks: $(grep -a -o -E 'no leaks found|leaks found: [0-9]+' "$RESULTS/gitleaks.txt" | tail -1)"; exit $rc
    ;;
  backup)
    # --no-deps: a compose file edited since the stack came up must never make a backup recreate the
    # database it is about to dump (2026-10-07: it did, once — three seconds of no chat database)
    lock backup
    one backup 900 docker compose -p ai-gateway -f docker-compose.webchat.yml run --rm --no-deps backup now
    say "backup: $(grep -a -E 'summary:' "$RESULTS/backup.txt" | tail -1 | cut -c1-170)"
    ;;
  *)
    sed -n '2,31p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 2
    ;;
esac
