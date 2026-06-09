#!/bin/sh
set -e

# Generate the SCRAM userlist from env at startup. For auth_type =
# scram-sha-256, PgBouncer accepts a PLAINTEXT password here and derives the
# SCRAM proof itself, so we don't need to pre-compute a verifier.
#
# If /etc/pgbouncer is read-only in your image build, write to a writable
# path and point auth_file there, or (dev only, internal network) switch
# auth_type to "trust" — see SCALING_ACTIVATION_RUNBOOK.md.
printf '"%s" "%s"\n' "${POSTGRES_USER}" "${POSTGRES_PASSWORD}" > /etc/pgbouncer/userlist.txt

exec pgbouncer /etc/pgbouncer/pgbouncer.ini
