#!/usr/bin/env bash
# AM21 §6.5 — the gallery: every layout of the report and the deck, with short, typical, long and absurd
# content, in both languages and under two themes, rendered by the engine (in the sandbox image), rasterised
# by the converter image's own LibreOffice at 30 dpi in grey, and compared with the approved pictures held in
# backend/agent_market/tests/fixtures/look_gallery. A picture that differs beyond the threshold fails, with
# both pictures side by side under the results folder; an absurd case that was not refused fails.
#
#   bash scripts/agent-market/look-gallery.sh            # the gate's lane: exit 1 on any difference
#   bash scripts/agent-market/look-gallery.sh approve    # after looking at the pictures: take them as approved
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -W 2>/dev/null || pwd)"
SANDBOX="${AM_SANDBOX_IMAGE:-ai-gateway-sandbox:latest}"
CONVERTER="${AM_CONVERTER_IMAGE:-ai-gateway-doc-converter:latest}"
OUT="$ROOT/scripts/agent-market/results/look-gallery"
bash "$ROOT/scripts/ops/power.sh" need "the look gallery" || exit 3
mkdir -p "$OUT"
rm -rf "$OUT"/*__*  "$OUT/_png" "$OUT/_pdf" "$OUT/_diff"

run_py() { # in the sandbox image, the checkout's engine mounted read-only, the results folder writable
  MSYS_NO_PATHCONV=1 docker run --rm --network none \
    -e HOME=/tmp -e MPLCONFIGDIR=/tmp/mpl -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONPATH=/src:/sandbox/scripts \
    -v "$ROOT/backend:/src:ro" -w /src \
    -v "$ROOT/services/chat-edge-api/scripts:/sandbox/scripts:ro" \
    -v "$OUT:/gallery" -v "$ROOT/backend/agent_market/tests/fixtures/look_gallery:/approved" \
    --entrypoint python3 "$SANDBOX" -m agent_market.tools.look_gallery "$@"
}

echo "== render (sandbox $SANDBOX)"
run_py render --out /gallery || { echo "lane_exit=1 (render failed)"; exit 1; }

echo "== rasterise (converter $CONVERTER)"
MSYS_NO_PATHCONV=1 docker run --rm --network none --user 0 -v "$OUT:/work" --entrypoint sh "$CONVERTER" -c '
set -e
export HOME=/tmp
mkdir -p /work/_pdf /work/_png
find /work -mindepth 2 -maxdepth 2 \( -name "*.docx" -o -name "*.pptx" \) | sort | while read -r f; do
  rel=$(echo "${f#/work/}" | tr "/" "__")
  base="${rel%.*}"
  ext="${f##*.}"
  case "$ext" in docx) filt=writer_pdf_Export;; pptx) filt=impress_pdf_Export;; esac
  cp "$f" "/tmp/$rel"
  soffice -env:UserInstallation=file:///tmp/lo --headless --norestore --nologo --convert-to "pdf:$filt" --outdir /tmp/out "/tmp/$rel" >/dev/null 2>&1 || echo "FAILED convert $rel"
  if [ -f "/tmp/out/${rel%.*}.pdf" ]; then
    mv "/tmp/out/${rel%.*}.pdf" "/work/_pdf/$base.pdf"
    pdftoppm -r 30 -gray -png "/work/_pdf/$base.pdf" "/work/_png/$base"
  fi
done
chmod -R a+rw /work/_pdf /work/_png 2>/dev/null || true
' || { echo "lane_exit=1 (rasterise failed)"; exit 1; }
echo "pictures: $(ls "$OUT/_png" | wc -l | tr -d ' ')"

echo "== compare"
if [ "${1:-}" = "approve" ]; then
  run_py compare --out /gallery --approved /approved --approve
else
  run_py compare --out /gallery --approved /approved
fi
rc=$?
echo "lane_exit=$rc"
exit $rc
