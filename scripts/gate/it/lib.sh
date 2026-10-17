#!/usr/bin/env bash
# AM24 — one chunk of an integration lane, the same way in every lane script (sourced; ROOT set by the caller).
#
# Until AM24 each lane script ran its chunks with a loop of its own, and three of them (AM10's, AM12's, AM13's)
# passed a chunk whose selector matched no test at all. Now a chunk is red when it ran no test, and when any name
# its selector lists ran as no test — a renamed or deleted test no longer leaves its chunk green with one fewer
# (scripts/gate/ledger.py chunk).
#
#   run_chunk '^(TestA|TestB)$'     prints "== chunk …", the verdict lines, and returns 1 on anything but all green
run_chunk() {
  local c="$1" tmp st
  echo "== chunk $c"
  tmp=$(mktemp "${TMP:-/tmp}/it_chunk.XXXXXX")
  timeout 1500 bash "$ROOT/scripts/gate/go_test.sh" ./internal/agentmarket/runner/ -run "$c" -count=1 -v -timeout 1400s > "$tmp" 2>&1
  st=$?
  grep -a -E '^(--- (PASS|FAIL|SKIP)|PASS$|FAIL|ok |panic)|^\s+\S+_test\.go:[0-9]+: ' "$tmp" || true
  python "$ROOT/scripts/gate/ledger.py" chunk "$c" "$tmp" || st=1
  rm -f "$tmp"
  return "$st"
}
