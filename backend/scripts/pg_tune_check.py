"""Verify Postgres write-tuning flags applied by docker-compose.

Run from inside the backend container (or anywhere with the same
``POSTGRES_*`` env) after a stack restart to confirm the postgres
service picked up the ``-c`` flags from docker-compose.yml.

Usage::

    docker compose exec backend python -m scripts.pg_tune_check

Exits 0 when every expected setting matches; 1 otherwise (so it can
gate CI / smoke tests).
"""

from __future__ import annotations

import sys

from sqlalchemy import text

from app.core.db import engine


_EXPECTED = {
    "synchronous_commit": "off",
    "max_connections": "200",
    "shared_buffers": "256MB",
    "wal_writer_delay": "200ms",
    "checkpoint_completion_target": "0.9",
    "effective_cache_size": "512MB",
}


def _show(conn, name: str) -> str:
    row = conn.execute(text(f"SHOW {name}")).first()
    return str(row[0]) if row else ""


def main() -> int:
    bad: list[tuple[str, str, str]] = []
    print("== Postgres tune check ==")
    with engine.connect() as conn:
        for name, want in _EXPECTED.items():
            got = _show(conn, name)
            ok = got.lower() == want.lower()
            marker = "OK" if ok else "FAIL"
            print(f"  [{marker}] {name:30s} expected={want!r:12s} got={got!r}")
            if not ok:
                bad.append((name, want, got))
    if bad:
        print(f"\n{len(bad)} setting(s) do not match — check docker-compose.yml "
              "postgres ``command:`` flags and restart the service.")
        return 1
    print("\nAll Postgres tuning flags applied correctly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
