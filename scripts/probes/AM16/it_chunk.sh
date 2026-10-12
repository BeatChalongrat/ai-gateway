#!/usr/bin/env bash
# AM16 — one chunk of the integration lane, alone, in the foreground (AM15's it_chunk.sh, with this
# card's lane added: the chunks of every lane scripts/gate.sh it runs, in its order).
#
# The whole lane as one command runs in the background, where a host short of memory gets it stopped
# with no verdict. One chunk is at most seven minutes. This runs chunk <n> (0-based) through
# scripts/gate.sh — the lock, and the wait for free memory — and appends its verdict lines to
# $GATE_RESULTS/it_clean.txt.
#
#   bash scripts/probes/AM16/it_chunk.sh list      the chunks
#   bash scripts/probes/AM16/it_chunk.sh <n>       run one
#   bash scripts/probes/AM16/it_chunk.sh verdict   what the file holds: passed, failed, chunks run
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT"
export GATE_RESULTS="${GATE_RESULTS:-scripts/probes/AM16/results/gate}" AGENT_MARKET_IT=1
mkdir -p "$GATE_RESULTS"
OUT="$GATE_RESULTS/it_clean.txt"
mapfile -t CHUNKS < <(for f in scripts/probes/AM10/it_lane.sh scripts/probes/AM12/it_lane.sh scripts/probes/AM13/it_lane.sh scripts/probes/AM15/it_lane.sh scripts/probes/AM16/it_lane.sh; do
  grep -o "^ *'\^(.*)\$'" "$f" | sed "s/^ *'//; s/'\$//"
done)
case "${1:-list}" in
  list) for i in "${!CHUNKS[@]}"; do echo "$i ${CHUNKS[$i]:0:110}"; done ;;
  verdict)
    echo "chunks run: $(grep -a -c '^== chunk ' "$OUT") of ${#CHUNKS[@]} · passed $(grep -a -c '^--- PASS' "$OUT") · failed $(grep -a -c '^--- FAIL' "$OUT") · skipped $(grep -a -c '^--- SKIP' "$OUT") · chunk exits not 0: $(grep -a -c '^chunk_exit=[^0]' "$OUT") · chunks that ran no test: $(grep -a -c '^chunk_ran_nothing' "$OUT")"
    ;;
  *)
    n="$1"; c="${CHUNKS[$n]:?no such chunk}"
    { echo "== chunk $n at $(git rev-parse --short=7 HEAD) $(date -u +%H:%M:%SZ) $c"; } >> "$OUT"
    tmp="$GATE_RESULTS/it_chunk_$n.txt"
    bash scripts/gate.sh walk -- timeout 570 bash scripts/probes/AM7/go_test.sh ./internal/agentmarket/runner/ -run "$c" -count=1 -v -timeout 540s > "$tmp" 2>&1
    rc=$?
    grep -a -E '^(--- (PASS|FAIL|SKIP)|ok |FAIL|panic)|^\s+\S+_test\.go:[0-9]+: [^t]|gate (STOPPED|REFUSED)' "$tmp" >> "$OUT"
    # a chunk that ran nothing is not a chunk that passed
    grep -a -q '^--- PASS' "$tmp" || { echo "chunk_ran_nothing $n" >> "$OUT"; rc=1; }
    echo "chunk_exit=$rc" >> "$OUT"
    echo "chunk $n exit=$rc · $(grep -a -c '^--- PASS' "$tmp") passed · $(grep -a -c '^--- FAIL' "$tmp") failed · $(grep -a -E '^ok |^FAIL\s' "$tmp" | tail -1 | awk '{print $NF}')"
    grep -a -E '^--- FAIL|gate (STOPPED|REFUSED)' "$tmp" | cut -c1-200 || true
    ;;
esac
