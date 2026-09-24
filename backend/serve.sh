#!/bin/sh
# FIX131 — the backend image's process (Dockerfile CMD).
#
#   1. forget the previous run's state (a `docker restart` keeps /tmp): the
#      worker board, the multiprocess metrics and the ``draining`` marker that
#      backend/healthcheck.py reads — gunicorn.conf.py's on_starting wipes it
#      again, this covers the migration window before gunicorn starts;
#   2. apply pending migrations ONCE per container, before any worker boots;
#   3. exec gunicorn, so it is PID 1 and receives Docker's SIGTERM itself
#      (the old CMD ran it as a child of `sh -c`, which as PID 1 ignores
#      SIGTERM: every stop waited for the SIGKILL).
#
# A SIGTERM during step 2 cannot interrupt alembic (Docker signals PID 1 only);
# the trap exits as soon as the migration returns instead of starting gunicorn.
set -e
trap 'exit 143' TERM INT
rm -rf "${SRAN_RUNTIME_DIR:-/tmp/sran-backend}"
python -m alembic upgrade head
exec gunicorn -c gunicorn.conf.py app.main:app
