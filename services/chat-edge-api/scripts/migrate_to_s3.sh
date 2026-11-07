#!/usr/bin/env bash
# Migrate every blob under the local storage tree (./data/uploads by
# default) into the configured S3 bucket, preserving the key layout
# the application uses.
#
# What it does:
#   1. Walks every regular file under SOURCE_DIR.
#   2. Strips the SOURCE_DIR prefix to recover the storage key.
#   3. Copies the file to s3://${S3_BUCKET}/${key} via the ``mc``
#      MinIO client (works against AWS S3 + MinIO + any compatible
#      service).
#
# After this finishes, flip STORAGE_BACKEND=s3 in chat-edge-api's env
# and restart. The DB rows already store the same key in
# ``storage_path`` (no DB migration required — Session 6 made the
# refactor deliberately) so existing files keep resolving.
#
# Required tools:
#   - bash 4+, find
#   - mc (https://min.io/docs/minio/linux/reference/minio-mc.html)
#
# Required env:
#   S3_ENDPOINT     (default: http://localhost:9000 — MinIO)
#   S3_BUCKET       (default: sran-chat-files)
#   S3_ACCESS_KEY   (default: minioadmin)
#   S3_SECRET_KEY   (default: minioadmin123)
#   SOURCE_DIR      (default: ./data/uploads — chat-edge default root)
#
# Usage:
#   ./scripts/migrate_to_s3.sh                       # dry-run summary
#   ./scripts/migrate_to_s3.sh --apply               # actually copy

set -euo pipefail

S3_ENDPOINT="${S3_ENDPOINT:-http://localhost:9000}"
S3_BUCKET="${S3_BUCKET:-sran-chat-files}"
S3_ACCESS_KEY="${S3_ACCESS_KEY:-minioadmin}"
S3_SECRET_KEY="${S3_SECRET_KEY:-minioadmin123}"
SOURCE_DIR="${SOURCE_DIR:-./data/uploads}"

APPLY=0
if [[ "${1:-}" == "--apply" ]]; then
  APPLY=1
fi

if [[ ! -d "$SOURCE_DIR" ]]; then
  echo "error: SOURCE_DIR not found: $SOURCE_DIR" >&2
  exit 1
fi

if ! command -v mc >/dev/null 2>&1; then
  echo "error: 'mc' (MinIO client) not found in PATH" >&2
  echo "  install: https://min.io/docs/minio/linux/reference/minio-mc.html" >&2
  exit 1
fi

ALIAS="sran-migrate"
mc alias set "$ALIAS" "$S3_ENDPOINT" "$S3_ACCESS_KEY" "$S3_SECRET_KEY" >/dev/null

# Make sure the bucket exists. No-op if already present.
mc mb --ignore-existing "$ALIAS/$S3_BUCKET" >/dev/null

# Normalise SOURCE_DIR to its absolute realpath so the prefix strip
# below is deterministic regardless of how the caller passed it.
SOURCE_DIR_ABS="$(cd "$SOURCE_DIR" && pwd)"
echo "Source:      $SOURCE_DIR_ABS"
echo "Destination: $ALIAS/$S3_BUCKET  (endpoint $S3_ENDPOINT)"
if [[ $APPLY -eq 0 ]]; then
  echo "Mode:        DRY-RUN (re-run with --apply to upload)"
else
  echo "Mode:        APPLY"
fi
echo

count=0
total_bytes=0
while IFS= read -r -d '' file; do
  rel="${file#$SOURCE_DIR_ABS/}"
  # POSIX find emits files; skip anything that managed to be a dir.
  [[ -f "$file" ]] || continue
  size=$(stat -c '%s' "$file" 2>/dev/null || stat -f '%z' "$file")
  count=$((count + 1))
  total_bytes=$((total_bytes + size))
  echo "  $rel  (${size} bytes)"
  if [[ $APPLY -eq 1 ]]; then
    mc cp --quiet "$file" "$ALIAS/$S3_BUCKET/$rel" >/dev/null
  fi
done < <(find "$SOURCE_DIR_ABS" -type f -print0)

echo
echo "Files: $count"
echo "Bytes: $total_bytes"
if [[ $APPLY -eq 0 ]]; then
  echo
  echo "Dry-run complete. Re-run with --apply to perform the upload."
fi
