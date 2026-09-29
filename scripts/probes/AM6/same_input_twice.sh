#!/usr/bin/env bash
# AM6 gate — the same input twice: the whole eval sets of the invoice ↔ PO
# agent and of the bank reconciliation, run in the sandbox image in two
# separate containers with two different hash seeds, then compared byte for
# byte — the computed table, the matching results (every link, status, reason
# code, match depth and cumulative figure), the cards that were asked, every
# cell of the working paper.
#
#   bash scripts/probes/AM6/same_input_twice.sh        # prints the number of differing lines (0) and exits 0
#
# No network, read-only source, nothing of the live system is touched.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd -W 2>/dev/null || pwd)"
IMAGE="${AM_SANDBOX_IMAGE:-ai-gateway-sandbox:latest}"
OUT="$ROOT/scripts/probes/AM6/results/twice"
rm -rf "$OUT"
mkdir -p "$OUT/a" "$OUT/b"
pass() { # <dir> <hash seed>
  MSYS_NO_PATHCONV=1 docker run --rm --network none \
    -e HOME=/tmp -e MPLCONFIGDIR=/tmp/mpl -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONHASHSEED="$2" \
    -v "$ROOT/backend:/src:ro" -w /src \
    -v "$ROOT/services/chat-edge-api/scripts:/sandbox/scripts:ro" \
    -v "$ROOT/scripts/probes/AM6/same_input_twice.py:/probe/same_input_twice.py:ro" \
    -v "$OUT/$1:/out" \
    --entrypoint python3 "$IMAGE" \
    -c "import sys, runpy; sys.path[0:0]=['/src','/sandbox/scripts']; sys.argv=['same_input_twice.py','/out']; runpy.run_path('/probe/same_input_twice.py', run_name='__main__')"
}
pass a 1 || { echo "first pass failed"; exit 2; }
pass b 987654321 || { echo "second pass failed"; exit 2; }
files=$(cd "$OUT/a" && ls | wc -l)
other=$(cd "$OUT/b" && ls | wc -l)
lines=$(diff -r "$OUT/a" "$OUT/b" | wc -l)
bytes=0
for f in "$OUT"/a/*; do
  cmp -s "$f" "$OUT/b/$(basename "$f")" || bytes=$((bytes + 1))
done
matching=$(diff <(cat "$OUT"/a/*.matching.json) <(cat "$OUT"/b/*.matching.json) | wc -l)
po=$(cd "$OUT/a" && ls po.*.matching.json | wc -l)
bank=$(cd "$OUT/a" && ls bank.*.matching.json | wc -l)
echo "files: $files and $other · cases with matching results: invoice-PO $po, bank $bank · files that differ: $bytes · differing lines: $lines · differing lines in the matching results: $matching"
(cd "$OUT/a" && cat ./*.matching.json | sha256sum | cut -c1-16 | sed 's/^/matching results, pass 1: sha256 /')
(cd "$OUT/b" && cat ./*.matching.json | sha256sum | cut -c1-16 | sed 's/^/matching results, pass 2: sha256 /')
[ "$files" -gt 0 ] && [ "$files" = "$other" ] && [ "$bytes" = 0 ] && [ "$lines" = 0 ]
