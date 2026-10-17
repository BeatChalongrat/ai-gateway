#!/usr/bin/env bash
# The gate, one heavy thing at a time.
#
# This host is a 32 GB laptop that also runs live. A gate used to start a lane, the test stack, image
# builds and a desktop build as each came to mind; together they took the host to 1.1 GB free five times
# in one day, the integration lane was cut twice, and nothing had a verdict. Run alone, the integration
# lane takes 41 minutes and passes.
#
# So a gate is this script and nothing beside it. Every phase:
#   * is refused on battery — before the lock, and again before each lane of a run (scripts/ops/power.sh);
#   * takes the lock — a second heavy phase does not start while one runs;
#   * waits for the host to have GATE_MIN_FREE_MB free (default 3000), and gives up saying so;
#   * stops what the phase does not need (a lane runs with the test stack stopped);
#   * writes its output to GATE_RESULTS/<phase>.txt and prints one verdict line.
# Before anything, live's own memory settings are checked and put right (scripts/ops/live_memory.py):
# every live container keeps its floor and its turn, so what a gate starts is the kernel's first choice.
#
#   bash scripts/gate.sh preflight            live's memory guard, free memory, what is running
#   bash scripts/gate.sh lanes                engine · go units, vet, format · the WHOLE backend suite · the host's
#                                             own tests · the alert rules · web · admin · desktop
#   bash scripts/gate.sh lanes <name…>        those lanes only, one piece in the foreground — says PARTIAL, never a verdict
#   bash scripts/gate.sh it                   the integration lane, alone, each chunk's verdict as it ends
#   bash scripts/gate.sh build <sha>          the five images, then the VM's cache handed back
#   bash scripts/gate.sh stack <sha>          test stack + stand-in web + built clients (a walk needs them); red when an
#                                             image of <sha> is missing or a service it started is not healthy
#   bash scripts/gate.sh stack-stop           stop the test stack (nothing is removed)
#   bash scripts/gate.sh walk -- <command…>   one walk, with the lock held
#   bash scripts/gate.sh smoke <sha>          smoke on the test stack: flag on, the model taken away (must be RED,
#                                             naming it) and put back (must be GREEN), flag off, back on
#   bash scripts/gate.sh drill                tracks (nothing tracked that .gitignore excludes) + removal drill +
#                                             gitleaks on the unpushed commits
#   bash scripts/gate.sh backup               a live backup with its restore drill (never recreates a dependency)
#   bash scripts/gate.sh verdict              the gate's one verdict, from its ledger: green only when every lane, every
#                                             chunk of the integration lane, the build, the stack and the smoke have a
#                                             green result at HEAD — else the lanes that do not, each with its commit
#
# A card says what is its own:  GATE_RESULTS (where the files go) · GATE_IT (its integration lane, run
# after the AM10 and web chunks).
#
# On battery: every phase but preflight and stack-stop says REFUSED and exits 3 having started nothing.
# Until 12 Oct 2026 the power was a line printed before a lane; it said "battery" and the lane ran, at
# half speed. To run one phase on battery anyway:  RUN_ON_BATTERY=yes bash scripts/gate.sh <phase> …
#
# AM15 — the backend lane is the suite, not a list. Until then a card named the backend files its lane
# ran (GATE_BACKEND_TESTS, ten of 418), and a test in any other file could fail for months with no lane
# to say so: AM13b found 26 that did, and the suite's 661 tests marked `integration` — which every
# lane deselected — held 91. Nothing ran the host's own tests (scripts/tests, the smoke's unit tests,
# the alert rules' tests) but the person who wrote them. So `lanes` now runs:
#   backend_lane     every backend test not marked integration (5,104 on 12 Oct 2026, four minutes)
#   backend_marked   every one that is, held to scripts/gate/backend_marked_failing.txt by name
#                    (scripts/gate/marked.py): what still fails there is said on every run, a failure
#                    not on the list is red, and so is a name on the list that no longer fails
#   host_tests       scripts/tests/test_*.py · smoke_units  scripts/smoke/*.test.js · alert_rules  promtool
#   gateway_go · log_worker_go · portal · dlp_sidecar   the services' own tests, which no lane had
# No list of files is left to fall off, and there is no setting that takes a file out.
#
# AM24 — a green that means green. Every lane, when it ends, writes the commit it ran at, its count of checks and its
# result to the gate's ledger (scripts/gate/ledger.py → scripts/gate/results/ledger.jsonl): a lane whose output names
# no check that ran is red (matched nothing), one whose checks were all skipped is a skip (ran over nothing), and one
# that ran fewer checks than its last run is red unless scripts/gate/removed_checks.txt says what replaced them
# (disappeared). The host cannot hold the whole gate at once, so it runs in pieces; `verdict` is then the gate's
# verdict: green only when every piece is green at the same commit, with nothing uncommitted under it.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
RESULTS="${GATE_RESULTS:-scripts/gate/results}"
LANES="tracks python_lane look_gallery go_units go_vet gofmt backend_lane backend_marked host_tests smoke_units alert_rules gateway_go log_worker_go portal dlp_sidecar web_tsc web_vitest web_lints admin desktop"
# AM24: what the gate's verdict needs a green result of, at one commit — every lane, every chunk of the integration
# lane (scripts/gate/it/chunk.sh ids), the build, the test stack and the smoke on it
verdict_lanes() {
  echo "$LANES $(bash scripts/gate/it/chunk.sh ids 2>/dev/null | tr -d '\r' | tr '\n' ' ') build_images build_runner build_fetcher stack_up stack_web clients_build clients_up smoke_test_on smoke_dead_primary_red smoke_dead_primary_green smoke_test_off"
}
at_commit() { # the commit a run is for, and whether anything tracked is changed under it (GATE_FOR_SHA: a built commit)
  if [ -n "${GATE_FOR_SHA:-}" ]; then echo "${GATE_FOR_SHA:0:7} 0"; return; fi
  echo "$(git rev-parse --short=7 HEAD) $([ -n "$(git status --porcelain --untracked-files=no)" ] && echo 1 || echo 0)"
}
MIN_FREE="${GATE_MIN_FREE_MB:-3000}"
WAIT_S="${GATE_WAIT_S:-600}"
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

power() { # nothing heavy starts on battery: refused outright, or run because the command itself said so
  local line
  line=$(bash scripts/ops/power.sh need "$1" 2>&1) || { say "$line"; exit 3; }
  [ -n "$line" ] && say "$line"
  return 0
}
lock() {
  power "gate $1"
  if ! mkdir "$LOCK" 2>/dev/null; then
    say "REFUSED: another heavy phase holds the lock ($(cat "$LOCK/what" 2>/dev/null || echo unknown)) — one at a time. Stale? rmdir after checking: $LOCK"
    exit 3
  fi
  echo "$1 since $(date -u +%H:%M:%SZ) pid $$" > "$LOCK/what"
  trap 'rm -rf "$LOCK"' EXIT
}
room() { # wait for the host to have room; give up saying how much it had
  local waited=0 f
  power "the gate's next step" # (a run of lanes is an hour: the charger can be pulled between two of them)
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
backend_marked() { # AM15: the backend tests marked integration — run, and held to the list of the ones that still fail
  local out="$RESULTS/backend_marked.txt" start=$SECONDS rc at
  at=$(at_commit)
  timeout 900 bash scripts/backend-test-lane.sh -q -p no:cacheprovider -m integration -rfEs > "$out" 2>&1
  python scripts/gate/marked.py "$out" > "$RESULTS/backend_marked_verdict.txt" 2>&1; rc=$?
  GATE_COMMIT="${at% *}" GATE_DIRTY="${at#* }" python scripts/gate/ledger.py record backend_marked "$out" "$rc" | sed 's/^/    /'
  [ "${PIPESTATUS[0]}" = "0" ] || rc=1
  say "backend_marked: $([ "$rc" = "0" ] && echo 'nothing moved' || echo "RED (exit $rc)") in $((SECONDS - start)) s → $RESULTS/backend_marked_verdict.txt"
  # what still fails and what cannot run is said here, on every run — it is never a silent exclusion
  grep -a -E '^(pytest:|marked tests that fail|STILL FAILING|SKIPPED IN THIS LANE|RED|  [+-] )' "$RESULTS/backend_marked_verdict.txt" | cut -c1-600 | sed 's/^/    /'
  return "$rc"
}
one() { # one <name> <timeout s> <command…> → a file, a verdict line, and the run in the gate's ledger
  local name="$1" t="$2"; shift 2
  local out="$RESULTS/$name.txt" start=$SECONDS at
  at=$(at_commit) # (the commit at the start: a commit made while the lane runs is not what it ran)
  ( timeout "$t" "$@" > "$out" 2>&1; echo "exit=$?" >> "$out" )
  local rc; rc=$(grep -a '^exit=' "$out" | tail -1 | cut -d= -f2)
  say "$name: $([ "$rc" = "0" ] && echo ok || echo "FAILED (exit $rc)") in $((SECONDS - start)) s → $out"
  # AM24: and not ok when it ran nothing, or fewer checks than its last run with no line saying what replaced them
  GATE_COMMIT="${at% *}" GATE_DIRTY="${at#* }" python scripts/gate/ledger.py record "$name" "$out" "${rc:-1}" | sed 's/^/    /'
  local lr=${PIPESTATUS[0]}
  [ "$rc" = "0" ] && [ "$lr" = "0" ]
}

phase="${1:-}"; shift || true
case "$phase" in
  preflight)
    guard
    say "host free: $(free_mb) MB · test containers up: $(test_containers | wc -l | tr -d ' ') · lock: $([ -d "$LOCK" ] && cat "$LOCK/what" || echo free)"
    ;;
  lanes)
    # lanes [name…] — the named lanes only: one piece, in the foreground (the whole run is over an hour,
    # and a long run in the background is stopped on this host when memory is short, with no verdict).
    # A run of pieces says PARTIAL and names what it did not run: it is never a gate's verdict.
    only=" $* "; ran=(); left=()
    for name in "$@"; do # (a name that is no lane is refused before anything is touched)
      case " $LANES " in *" $name "*) ;; *) say "REFUSED: no such lane: $name (the lanes are: $LANES)"; exit 2 ;; esac
    done
    want() { [ "$only" = "  " ] && return 0; case "$only" in *" $1 "*) return 0 ;; *) return 1 ;; esac; }
    lane() { # lane <name> <timeout s> <command…>
      case " $LANES " in *" $1 "*) ;; *) say "STOPPED: lane $1 is not in the list of lanes — add it to LANES"; exit 5 ;; esac
      if want "$1"; then ran+=("$1"); room; one "$@" || rc=1; else left+=("$1"); fi
    }
    lock lanes; stack_stop; room; guard; rc=0
    # AM19: only what the code needs is tracked — a tracked file that .gitignore excludes fails, by name
    lane tracks 60 python scripts/gate/tracks.py
    lane python_lane 3000 bash scripts/agent-market/python-lane.sh
    # AM21: every layout at every content length, rendered and compared with the approved pictures
    lane look_gallery 1500 bash scripts/agent-market/look-gallery.sh
    lane go_units 2700 bash scripts/gate/go_test.sh ./... -count=1
    lane go_vet 600 bash -c 'cd services/chat-edge-api && go vet ./...'
    lane gofmt 120 bash -c 'cd services/chat-edge-api && test -z "$(gofmt -l internal/agentmarket cmd/agent-runner)"'
    # AM15: the suite, not a list of its files — and the tests marked integration, by name
    lane backend_lane 1500 bash scripts/backend-test-lane.sh -q -p no:cacheprovider -m "not integration" -rfEsx
    want backend_lane && say "backend_lane: $(grep -a -E '^(=+ )?[0-9]+ (passed|failed)' "$RESULTS/backend_lane.txt" | tail -1 | tr -d '=' | cut -c1-120)"
    if want backend_marked; then ran+=(backend_marked); room; backend_marked || rc=1; else left+=(backend_marked); fi
    lane host_tests 600 python -m unittest discover -s scripts/tests -p 'test_*.py'
    lane smoke_units 300 bash -c 'node --test scripts/smoke/*.test.js'
    lane alert_rules 300 env MSYS_NO_PATHCONV=1 docker run --rm --network none --entrypoint promtool -v "$(pwd -W 2>/dev/null || pwd)/infra/prometheus:/p:ro" -w /p prom/prometheus:v2.53.0 test rules tests/agent_market_test.yml
    # … and the test trees of the services no lane had: the Go gateway, the log worker, the portal, and
    # the privacy sidecar's own (Rust, in its builder image — no network, nothing built into an image)
    lane gateway_go 600 bash -c 'cd services/gateway && go test ./... -count=1'
    lane log_worker_go 600 bash -c 'cd log-worker && go test ./... -count=1'
    lane portal 600 bash -c 'cd portal && npx vitest run'
    lane dlp_sidecar 2400 env MSYS_NO_PATHCONV=1 docker run --rm --network none -v "$(pwd -W 2>/dev/null || pwd)/dlp-sidecar/src:/src/src:ro" -v "$(pwd -W 2>/dev/null || pwd)/dlp-sidecar/tests:/src/tests:ro" -v "$(pwd -W 2>/dev/null || pwd)/dlp-sidecar/data:/src/data:ro" "${GATE_SIDECAR_BUILDER:-dlp-sidecar-builder:fix103}" cargo test --locked --offline --release
    lane web_tsc 900 bash -c 'cd apps/web-chat && npx tsc --noEmit -p .'
    lane web_vitest 1500 bash -c 'cd apps/web-chat && npx vitest run'
    lane web_lints 600 bash -c 'cd apps/web-chat && npm run -s lint:i18n && npm run -s check:classes && npm run -s lint:theme'
    lane admin 900 bash -c 'cd admin-ui && npx tsc --noEmit -p . && npx vitest run'
    lane desktop 900 bash -c 'cd apps/desktop && npm run -s typecheck && npx vitest run'
    if [ "${#left[@]}" -gt 0 ]; then
      say "lanes: PARTIAL — ran ${ran[*]}: $([ $rc = 0 ] && echo ok || echo RED) · NOT run here: ${left[*]}"
      say "$(python scripts/gate/ledger.py verdict $(verdict_lanes))" # (AM24: the pieces' verdict is the ledger's)
      exit $rc
    fi
    # (not "all green" while backend tests stand on the list of the ones that still fail)
    standing=$(grep -a -o -E 'on the list: [0-9]+' "$RESULTS/backend_marked_verdict.txt" 2>/dev/null | grep -o '[0-9]*$' || echo '?')
    say "lanes: $([ $rc = 0 ] && { [ "$standing" = 0 ] && echo ALL GREEN || echo "GREEN, with $standing backend test(s) still failing by name (above) — not green until that list is empty"; } || echo RED)"
    say "$(python scripts/gate/ledger.py verdict $(verdict_lanes))"; exit $rc
    ;;
  it)
    lock it; stack_stop; room; guard
    export AGENT_MARKET_IT=1
    # AM21: the converter the integration tests start is the one this commit built (AM_CONVERTER_TAG), not a stale :dev
    export AM_CONVERTER_IMAGE="${AM_CONVERTER_IMAGE:-ai-gateway-doc-converter:${AM_CONVERTER_TAG:-latest}}"
    out="$RESULTS/it_lane.txt"; : > "$out"; rc=0; at=$(at_commit)
    {
      date -u +"started %H:%M:%SZ at $(git rev-parse --short=7 HEAD)"
      timeout 4200 bash scripts/gate/it/am10.sh | sed -u 's/^lane_exit=/am10_lane_exit=/'
      timeout 2400 bash scripts/gate/it/am12.sh web | sed -u 's/^lane_exit=/am12_web_lane_exit=/'
      [ -n "${GATE_IT:-scripts/gate/it/am23.sh}" ] && timeout 2400 bash ${GATE_IT:-scripts/gate/it/am23.sh} | sed -u 's/^lane_exit=/card_lane_exit=/'
      date -u +"ended %H:%M:%SZ"
    } >> "$out" 2>&1
    grep -a -q -E '^(--- FAIL|FAIL|panic)' "$out" && rc=1
    grep -a -E '_lane_exit=' "$out" | grep -v -q '=0$' && rc=1
    [ "$(grep -a -c -E '_lane_exit=0$' "$out")" -ge 3 ] || rc=1
    # AM24: each chunk of the file in the gate's ledger — a file emptied partway, or a chunk short of a name, is red
    GATE_COMMIT="${at% *}" GATE_DIRTY="${at#* }" python scripts/gate/ledger.py it-lane "$out" > "$RESULTS/it_ledger.txt" 2>&1 || rc=1
    grep -a -v ' GREEN ' "$RESULTS/it_ledger.txt" | sed 's/^/    /' | head -20
    say "integration: $(grep -a -c '^--- PASS' "$out") passed, $(grep -a -c '^--- FAIL' "$out") failed · $(grep -a -E '_lane_exit=' "$out" | tr '\n' ' ')→ $out"
    exit $rc
    ;;
  build)
    sha="${1:?the commit to build (7 characters)}"
    export GATE_FOR_SHA="$sha" # (AM24: the ledger records these lanes for the commit built, not the checkout's)
    lock build; stack_stop; room; guard; rc=0
    one build_images 2400 bash scripts/build-images.sh --rev "$sha" chat-edge-api web-chat admin-ui || rc=1
    room; one build_runner 1800 bash scripts/agent-market/build.sh "$sha" || rc=1
    one build_fetcher 1800 bash scripts/agent-market/build-fetcher.sh "$sha" || rc=1
    cache_back; say "build: $([ $rc = 0 ] && echo ok || echo FAILED) · host free now $(free_mb) MB"; exit $rc
    ;;
  stack)
    sha="${1:?the commit whose images the test stack runs}"
    export GATE_FOR_SHA="$sha"
    lock stack; room; guard; rc=0
    one stack_up 1200 env AM7_NO_NER=1 TS_BACKEND_TAG="${TS_BACKEND_TAG:-2fa9bb8}" AM_FETCHER_TAG="${AM_FETCHER_TAG:-$sha}" bash scripts/agent-market/test_stack.sh up "$sha" || rc=1
    # (the stand-in sites run on the fetcher's image: the card's own when it built one, else the one named)
    one stack_web 600 bash scripts/agent-market/test_stack_web.sh web "${AM_FETCHER_TAG:-$sha}" || rc=1
    one clients_build 1500 bash scripts/agent-market/test_clients.sh build "$sha" || rc=1
    one clients_up 600 bash scripts/agent-market/test_clients.sh up "$sha" || rc=1
    # AM19b: the phase is ok because the stack is what was asked (test_stack.sh check), not because it ran
    cache_back; say "stack: $([ $rc = 0 ] && echo ok || echo FAILED) · $(grep -a -E '^(stack check:|RED:)' "$RESULTS/stack_up.txt" | head -3 | tr '\n' ' ' | cut -c1-220)"; exit $rc
    ;;
  stack-stop) stack_stop ;;
  verdict)
    # AM24: no lock, nothing run — what the ledger holds for HEAD; the last line names what is not green, and where
    python scripts/gate/ledger.py verdict $(verdict_lanes)
    ;;
  walk)
    [ "${1:-}" = "--" ] && shift
    lock "walk $*"; room; guard
    "$@"
    ;;
  smoke)
    sha="${1:?the commit the test stack runs}"
    export GATE_FOR_SHA="$sha"
    lock smoke; guard; rc=0
    one smoke_test_on 600 env SMOKE_TARGET=test SMOKE_DESKTOP=0 bash scripts/smoke.sh || rc=1
    # AM15: the proof that the smoke can fail. The model is taken away on the test stack and the suite
    # must come out RED with every turn naming the model it asked; put back, it must come out GREEN
    # with every turn answered by it (scripts/smoke/dead_primary.js waits out the send limit itself).
    one smoke_dead_primary_red 900 env SMOKE_TARGET=test DEAD_PRIMARY_OUT="$(pwd -W 2>/dev/null || pwd)/$RESULTS" node scripts/smoke/dead_primary.js red || rc=1
    one smoke_dead_primary_green 600 env SMOKE_TARGET=test DEAD_PRIMARY_OUT="$(pwd -W 2>/dev/null || pwd)/$RESULTS" node scripts/smoke/dead_primary.js green || rc=1
    sleep 95 # (the gateway's rate limit: 90 s of quiet between two runs)
    bash scripts/agent-market/test_stack.sh edge "$sha" off > /dev/null 2>&1; docker stop sran-test-agent-runner > /dev/null 2>&1
    one smoke_test_off 600 env AGENT_MARKET_ENABLED=false SMOKE_TARGET=test SMOKE_DESKTOP=0 bash scripts/smoke.sh || rc=1
    bash scripts/agent-market/test_stack.sh edge "$sha" on > /dev/null 2>&1; AM7_NO_NER=1 bash scripts/agent-market/test_stack.sh runner "$sha" > /dev/null 2>&1
    exit $rc
    ;;
  drill)
    lock drill; rc=0
    one tracks 60 python scripts/gate/tracks.py || rc=1
    one removal_drill 1700 bash scripts/agent-market/removal_drill.sh || rc=1
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
    sed -n '2,/^set -uo pipefail/p' "${BASH_SOURCE[0]}" | sed '$d' | sed 's/^# \{0,1\}//'
    exit 2
    ;;
esac
