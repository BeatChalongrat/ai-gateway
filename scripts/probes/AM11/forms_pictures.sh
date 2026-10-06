#!/usr/bin/env bash
# AM11 (D) — a form and its filled copy as a person would see them printed: each file is turned
# into a PDF by the document converter (LibreOffice, the same one that makes a report's PDF) and its
# first page into a picture; forms_side_by_side.py then puts source and result in one row.
#
#   bash scripts/probes/AM11/forms_pictures.sh <folder> <source> <filled> [<source> <filled> …]
#
# <folder> receives <name>.pdf and <name>.png for every file given. The converter runs as a
# throwaway container on a loopback port; nothing here reaches the live stack or the test stack.
set -uo pipefail
export MSYS_NO_PATHCONV=1
ROOT=$(git rev-parse --show-toplevel)
OUT=$1
shift
mkdir -p "$OUT"
CONV=am11-forms-converter
IMG=${AM_CONVERTER_IMAGE:-ai-gateway-doc-converter:dev}
docker rm -f "$CONV" >/dev/null 2>&1
docker run -d --name "$CONV" -p 127.0.0.1:18997:8096 "$IMG" >/dev/null || exit 1
trap 'docker rm -f "$CONV" >/dev/null 2>&1' EXIT
for i in $(seq 1 40); do
  curl -s -o /dev/null -m 3 http://127.0.0.1:18997/health && break
  sleep 1
done
rc=0
for f in "$@"; do
  base=$(basename "$f")
  name=${base%.*}_${base##*.}
  code=$(curl -s -m 180 -o "$OUT/$name.pdf" -w '%{http_code}' -X POST -H "X-Filename: form.${base##*.}" \
    --data-binary "@$f" "http://127.0.0.1:18997/convert?to=pdf")
  if [ "$code" != 200 ]; then
    echo "convert $base: HTTP $code"
    rc=1
    continue
  fi
  docker cp "$OUT/$name.pdf" "$CONV:/tmp/$name.pdf" >/dev/null
  docker exec "$CONV" sh -c "cd /tmp && pdftoppm -png -r 110 -f 1 -l 1 -singlefile $name.pdf $name" || rc=1
  docker cp "$CONV:/tmp/$name.png" "$OUT/$name.png" >/dev/null || rc=1
  echo "$base → $name.png"
done
exit $rc
