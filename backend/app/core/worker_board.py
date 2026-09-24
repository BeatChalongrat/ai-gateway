"""FIX131 — what every worker process of THIS container says about itself.

``/ready`` is answered by whichever gunicorn worker the kernel hands the
connection to. Its engine checks (database, redis, DLP, loop lag) are
rightly per worker; some answers are about the container and must not depend
on the worker that happens to answer:

* ``stuck_requests`` — a request stuck past its bound in ANY worker makes the
  container not ready (FIX26's rule, which with one process was the same
  thing). Per worker, a stuck worker would fail one probe in N and the
  healthcheck's "3 in a row" would almost never trip;
* ``turns`` — when the container last completed a turn (turn_pulse), not
  when this worker did;
* which worker is the leader (app/core/leader.py).

Each worker writes a small JSON record every ``publish_s`` seconds into
``SRAN_WORKER_STATE_DIR`` (gunicorn.conf.py sets it and wipes it at start;
its child_exit hook deletes the record of a worker that exits); /ready reads
the others' records and ignores any older than ``max_age_s``. Without the
variable — a single process: dev hot-reload, the test lane — the board is
empty and /ready describes this process, exactly as before.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Callable
from pathlib import Path

ENV = "SRAN_WORKER_STATE_DIR"
PUBLISH_S = 5.0
# Three missed publications = gone (killed without child_exit, or wedged).
MAX_AGE_S = 3 * PUBLISH_S


def board_dir() -> Path | None:
    raw = (os.environ.get(ENV) or "").strip()
    return Path(raw) if raw else None


def publish(record: dict, *, pid: int | None = None, now: float | None = None) -> bool:
    """Write this worker's record atomically. False when there is no board."""
    d = board_dir()
    if d is None:
        return False
    pid = os.getpid() if pid is None else pid
    body = {**record, "pid": pid, "at": time.time() if now is None else now}
    try:
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f".{pid}.json.tmp"
        tmp.write_text(json.dumps(body), encoding="utf-8")
        os.replace(tmp, d / f"{pid}.json")
        return True
    except OSError:
        return False


def peers(*, max_age_s: float = MAX_AGE_S, now: float | None = None,
          exclude_pid: int | None = None) -> list[dict]:
    """Fresh records of the OTHER workers (unreadable or stale ones skipped)."""
    d = board_dir()
    if d is None:
        return []
    exclude_pid = os.getpid() if exclude_pid is None else exclude_pid
    now = time.time() if now is None else now
    out: list[dict] = []
    try:
        entries = list(d.glob("*.json"))
    except OSError:
        return []
    for p in entries:
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(rec, dict) or rec.get("pid") == exclude_pid:
            continue
        try:
            if now - float(rec.get("at", 0)) > max_age_s:
                continue
        except (TypeError, ValueError):
            continue
        out.append(rec)
    return sorted(out, key=lambda r: r.get("pid", 0))


def remove(pid: int | None = None) -> None:
    d = board_dir()
    if d is None:
        return
    try:
        (d / f"{os.getpid() if pid is None else pid}.json").unlink()
    except OSError:
        pass


async def publisher_loop(build: Callable[[], dict], *, interval_s: float = PUBLISH_S) -> None:
    """Background task: publish ``build()`` forever; never raises (except cancel)."""
    if board_dir() is None:
        return
    try:
        while True:
            try:
                publish(build())
            except Exception:  # noqa: BLE001 — telemetry never breaks the worker
                pass
            await asyncio.sleep(interval_s)
    finally:
        remove()
