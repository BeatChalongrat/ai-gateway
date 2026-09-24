"""FIX131 — the backend container's healthcheck (docker-compose.yml).

Healthy = ``GET /ready`` answers 2xx within 4 s (FIX26: /ready exercises the
real request path), EXCEPT while the container is draining after SIGTERM:
gunicorn.conf.py writes ``<SRAN_RUNTIME_DIR>/draining`` the moment the master
gets SIGTERM, the listening socket closes, and /ready becomes unreachable on
purpose. Reporting that as unhealthy would make autoheal restart a container
that is finishing its in-flight turns (and race `docker compose up`'s
recreate). Docker's stop_grace_period still bounds the drain.

Standard library only: it runs every 10 s in its own interpreter and must not
import the app.
"""

from __future__ import annotations

import os
import sys
import urllib.request

# Same default as gunicorn.conf.py (RUNTIME_DIR_DEFAULT) — a test pins them.
RUNTIME_DIR_DEFAULT = "/tmp/sran-backend"
READY_URL = "http://localhost:8080/ready"


def draining(runtime_dir: str | None = None) -> bool:
    base = runtime_dir or os.environ.get("SRAN_RUNTIME_DIR") or RUNTIME_DIR_DEFAULT
    return os.path.exists(os.path.join(base, "draining"))


def main() -> int:
    if draining():
        return 0
    try:
        urllib.request.urlopen(READY_URL, timeout=4)
    except Exception as exc:  # noqa: BLE001 — any failure is "unhealthy"
        print(f"unhealthy: {type(exc).__name__}: {str(exc)[:200]}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
