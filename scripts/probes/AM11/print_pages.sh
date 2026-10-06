#!/usr/bin/env bash
# AM11 (E) — the working papers on paper, AM10 against now: each agent's sample working paper is
# rendered by its own writer in the sandbox image (AM9's print_samples.py, this checkout's code),
# printed to PDF by the document converter, and its pages are counted beside AM10's own printed PDFs
# (scripts/probes/AM10/results/print). Page images of the widest paper's line sheet are kept.
#
#   bash scripts/probes/AM11/print_pages.sh        → results/print/{*.xlsx,*.pdf,pages.tsv,po-*.png}
#
# No network for the sandbox; the converter runs as a throwaway container on a loopback port.
set -uo pipefail
export MSYS_NO_PATHCONV=1
ROOT=$(git rev-parse --show-toplevel)
W() { if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf '%s' "$1"; fi; }
OUT="$ROOT/scripts/probes/AM11/results/print"
OLD="$ROOT/scripts/probes/AM10/results/print"
mkdir -p "$OUT"
CONV=am11-print-converter
IMG=${AM_CONVERTER_IMAGE:-ai-gateway-doc-converter:dev}

docker run --rm --network none -e AM9_OUT=/out \
  -v "$(W "$ROOT/backend"):/src:ro" -v "$(W "$ROOT/services/chat-edge-api/scripts"):/sandbox/scripts:ro" \
  -v "$(W "$ROOT/scripts/probes/AM9"):/am9:ro" -v "$(W "$OUT"):/out" \
  --entrypoint python3 ai-gateway-sandbox:latest /am9/print_samples.py || exit 1

docker rm -f "$CONV" >/dev/null 2>&1
docker run -d --name "$CONV" -p 127.0.0.1:18996:8096 "$IMG" >/dev/null || exit 1
trap 'docker rm -f "$CONV" >/dev/null 2>&1' EXIT
for i in $(seq 1 40); do
  curl -s -o /dev/null -m 3 http://127.0.0.1:18996/health && break
  sleep 1
done
pages() { docker exec "$CONV" sh -c "pdfinfo /tmp/$1 2>/dev/null | awk '/^Pages:/ {print \$2}'"; }
printf 'package\tpages_AM9\tpages_AM10\n' > "$OUT/pages.tsv"
for pkg in bank_reconciliation po_matching expense_audit invoice_processing financial_report; do
  code=$(curl -s -m 180 -o "$OUT/$pkg.pdf" -w '%{http_code}' -X POST -H "X-Filename: $pkg.xlsx" \
    --data-binary "@$OUT/$pkg.xlsx" "http://127.0.0.1:18996/convert?to=pdf")
  [ "$code" = 200 ] || { echo "convert $pkg: HTTP $code"; continue; }
  docker cp "$OUT/$pkg.pdf" "$CONV:/tmp/new_$pkg.pdf" >/dev/null
  before=-
  if [ -f "$OLD/$pkg.pdf" ]; then
    docker cp "$OLD/$pkg.pdf" "$CONV:/tmp/old_$pkg.pdf" >/dev/null
    before=$(pages "old_$pkg.pdf")
  fi
  printf '%s\t%s\t%s\n' "$pkg" "$before" "$(pages "new_$pkg.pdf")" >> "$OUT/pages.tsv"
done
# the widest paper: the PO line sheet and its companions, as printed
docker exec "$CONV" sh -c 'cd /tmp && pdftoppm -png -r 70 -f 1 -l 12 new_po_matching.pdf po && ls po-*.png' | tr -d '\r' | while read -r f; do
  docker cp "$CONV:/tmp/$f" "$OUT/$f" >/dev/null
done
docker exec "$CONV" sh -c 'cd /tmp && pdftotext -raw new_po_matching.pdf - | grep -c "" ' >/dev/null
cat "$OUT/pages.tsv"
