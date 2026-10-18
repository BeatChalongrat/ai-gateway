#!/usr/bin/env bash
# AM18 — one chunk of the integration lane, alone, in the foreground (AM16's it_chunk.sh, with this
# card's lane added: the chunks of every lane scripts/gate.sh it runs, in its order).
#
# The whole lane as one command runs in the background, where a host short of memory gets it stopped
# with no verdict. One chunk is at most seven minutes. This runs chunk <n> (0-based) through
# scripts/gate.sh — the lock, and the wait for free memory — and appends its verdict lines to
# $GATE_RESULTS/it_clean.txt.
#
#   bash scripts/gate/it/chunk.sh list      the chunks
#   bash scripts/gate/it/chunk.sh ids       their names in the gate's ledger (it:<first test>+<the others>)
#   bash scripts/gate/it/chunk.sh <n>       run one
#   bash scripts/gate/it/chunk.sh verdict   what the file holds: passed, failed, chunks run — RED unless every chunk ran
#
# AM24: each chunk's run is written to the gate's ledger (scripts/gate/ledger.py record-chunk): red when it failed,
# ran no test, or a name its selector lists ran as no test, and its count is held to its last run's. The gate is
# green only when every chunk has a green result at the commit (bash scripts/gate.sh verdict).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT"
export GATE_RESULTS="${GATE_RESULTS:-scripts/gate/results/it}" AGENT_MARKET_IT=1
# AM24: the converter a chunk starts is the one gate.sh it starts — this commit's built image, never a stale :dev. Until
# AM24 a chunk run on its own fell back to :dev, and the report's PDF failed its gate on a converter that does not
# measure margins (pdf_it_test.go), unless whoever ran it remembered to set this.
export AM_CONVERTER_IMAGE="${AM_CONVERTER_IMAGE:-ai-gateway-doc-converter:${AM_CONVERTER_TAG:-latest}}"
mkdir -p "$GATE_RESULTS"
OUT="$GATE_RESULTS/it_clean.txt"
mapfile -t CHUNKS < <(for f in scripts/gate/it/am10.sh scripts/gate/it/am12.sh scripts/gate/it/am13.sh scripts/gate/it/am15.sh scripts/gate/it/am16.sh scripts/gate/it/am18.sh scripts/gate/it/am22.sh scripts/gate/it/am23.sh scripts/gate/it/am24.sh; do
  grep -o "^ *'\^(.*)\$'" "$f" | sed "s/^ *'//; s/'\$//"
done)
case "${1:-list}" in
  list) for i in "${!CHUNKS[@]}"; do echo "$i ${CHUNKS[$i]:0:110}"; done ;;
  ids) for c in "${CHUNKS[@]}"; do python scripts/gate/ledger.py chunk-id "$c"; done ;;
  verdict)
    run=$(grep -a -c '^== chunk ' "$OUT" 2>/dev/null); run=${run:-0}
    bad=$(grep -a -c -E '^(--- FAIL|chunk_exit=[^0]|chunk_ran_nothing)' "$OUT" 2>/dev/null); bad=${bad:-0}
    echo "chunks run: $run of ${#CHUNKS[@]} · passed $(grep -a -c '^--- PASS' "$OUT") · failed $(grep -a -c '^--- FAIL' "$OUT") · skipped $(grep -a -c '^--- SKIP' "$OUT") · chunk exits not 0: $(grep -a -c '^chunk_exit=[^0]' "$OUT") · chunks that ran no test: $(grep -a -c '^chunk_ran_nothing' "$OUT")"
    # AM24: a file that holds fewer chunks than the lists, or none, is not a pass — nor one with a failure in it
    if [ "$run" -lt "${#CHUNKS[@]}" ] || [ "$bad" -gt 0 ]; then
      echo "RED: $([ "$run" -lt "${#CHUNKS[@]}" ] && echo "$((${#CHUNKS[@]} - run)) of the ${#CHUNKS[@]} chunks have no run in $OUT")$([ "$run" -lt "${#CHUNKS[@]}" ] && [ "$bad" -gt 0 ] && echo " · ")$([ "$bad" -gt 0 ] && echo "$bad failure line(s)")"
      exit 1
    fi
    ;;
  *)
    n="$1"; c="${CHUNKS[$n]:?no such chunk}"
    { echo "== chunk $n at $(git rev-parse --short=7 HEAD) $(date -u +%H:%M:%SZ) $c"; } >> "$OUT"
    tmp="$GATE_RESULTS/it_chunk_$n.txt"
    bash scripts/gate.sh walk -- timeout 570 bash scripts/gate/go_test.sh ./internal/agentmarket/runner/ -run "$c" -count=1 -v -timeout 540s > "$tmp" 2>&1
    rc=$?
    grep -a -E '^(--- (PASS|FAIL|SKIP)|ok |FAIL|panic)|^\s+\S+_test\.go:[0-9]+: [^t]|gate (STOPPED|REFUSED)' "$tmp" >> "$OUT"
    # a chunk that ran nothing is not a chunk that passed
    grep -a -q '^--- PASS' "$tmp" || { echo "chunk_ran_nothing $n" >> "$OUT"; rc=1; }
    # AM24: nor one in which a name it lists ran as no test; and its run goes to the gate's ledger
    python scripts/gate/ledger.py record-chunk "$c" "$tmp" "$rc" >> "$OUT" || rc=1
    # and every name the chunks list, all of them, held to the last green run's: a name gone from every list is red
    python scripts/gate/ledger.py selectors it_names "${CHUNKS[@]}" >> "$OUT" || rc=1
    echo "chunk_exit=$rc" >> "$OUT"
    echo "chunk $n exit=$rc · $(grep -a -c '^--- PASS' "$tmp") passed · $(grep -a -c '^--- FAIL' "$tmp") failed · $(grep -a -E '^ok |^FAIL\s' "$tmp" | tail -1 | awk '{print $NF}')"
    grep -a -E '^--- FAIL|gate (STOPPED|REFUSED)' "$tmp" | cut -c1-200 || true
    ;;
esac
