# syntax=docker/dockerfile:1.7
# FIX129 A5 — the backup service (docs/ops/backups.md).
#
# postgres:16 ships pg_dump / pg_restore / psql / createdb / dropdb of the
# same major version as the live servers (pgvector/pgvector:pg16). On top:
#   curl + ca-certificates  the alert channels (ntfy, Resend)
#   tzdata                  BACKUP_AT is a wall-clock time in $TZ
#   jq                      manifest.json / drill.json are written and read
#                           with it instead of hand-assembled JSON
# The postgres image's own entrypoint (a database server) is NOT used; the
# scripts are the entrypoint.
FROM postgres:16

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl ca-certificates tzdata jq \
    && rm -rf /var/lib/apt/lists/*

COPY lib.sh backup.sh drill.sh notify.sh entrypoint.sh /
RUN chmod 0755 /backup.sh /drill.sh /notify.sh /entrypoint.sh

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
