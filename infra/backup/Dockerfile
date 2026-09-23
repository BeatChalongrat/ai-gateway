# syntax=docker/dockerfile:1.7
# FIX129 A5 — the backup service (docs/ops/backups.md).
#
# postgres:16 ships pg_dump / pg_restore / psql / createdb / dropdb of the
# same major version as the live servers (pgvector/pgvector:pg16). On top:
#   curl + ca-certificates  the alert channels (ntfy, Resend)
#   tzdata                  BACKUP_AT is a wall-clock time in $TZ
#   jq                      manifest.json / drill.json are written and read
#                           with it instead of hand-assembled JSON
#   bzip2                   unpacks the restic release binary (below)
#   restic                  FIX130 E1 — the off-site copy (offsite.sh)
# The postgres image's own entrypoint (a database server) is NOT used; the
# scripts are the entrypoint.
FROM postgres:16

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl ca-certificates tzdata jq bzip2 \
    && rm -rf /var/lib/apt/lists/*

# restic, pinned. The official release binary, checked against the SHA-256
# values of the release's SHA256SUMS file; that file's signature
# (SHA256SUMS.asc) was verified on 2026-09-23 against the restic release key
# CF8F 18F2 8445 7597 3F79 D4E1 91A6 868B D3F7 A907 (Alexander Neumann) —
# "Good signature". To upgrade: new version + both sums from the new
# SHA256SUMS, after verifying its signature the same way.
ARG RESTIC_VERSION=0.19.1
ARG RESTIC_SHA256_AMD64=f415415624dcc452f2a02b8c33641791a8c6d6d3b65bbb3543fcf9a25151585c
ARG RESTIC_SHA256_ARM64=a5f64aaab53d51e311fa3829124c5b703f2d14cf187d8640b6be3b2b49376465
ARG TARGETARCH=amd64
RUN set -eu; \
    case "$TARGETARCH" in \
      amd64) sum="$RESTIC_SHA256_AMD64" ;; \
      arm64) sum="$RESTIC_SHA256_ARM64" ;; \
      *) echo "no pinned restic checksum for $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/restic.bz2 \
      "https://github.com/restic/restic/releases/download/v${RESTIC_VERSION}/restic_${RESTIC_VERSION}_linux_${TARGETARCH}.bz2"; \
    echo "$sum  /tmp/restic.bz2" | sha256sum -c -; \
    bunzip2 -c /tmp/restic.bz2 > /usr/local/bin/restic; \
    rm /tmp/restic.bz2; \
    chmod 0755 /usr/local/bin/restic; \
    restic version

COPY lib.sh backup.sh drill.sh notify.sh offsite.sh entrypoint.sh /
RUN chmod 0755 /backup.sh /drill.sh /notify.sh /offsite.sh /entrypoint.sh

# Where the scripts read from / write to. Both are mounts in compose
# (chat_edge_uploads:/uploads:ro and ${BACKUP_DIR}:/backups).
VOLUME ["/backups"]

# FIX126 O6 - provenance: every build carries the commit it was built from
# (compose passes REVISION/CREATED as build args). Declared at the END so a
# new revision never busts the cached layers above.
ARG REVISION=unknown
ARG CREATED=unknown
LABEL org.opencontainers.image.revision=$REVISION \
      org.opencontainers.image.created=$CREATED

ENTRYPOINT ["/entrypoint.sh"]
# No CMD: no argument = the scheduled loop; `now` = one backup + drill.
