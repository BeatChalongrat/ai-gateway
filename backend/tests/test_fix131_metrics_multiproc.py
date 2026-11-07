"""FIX131 — /metrics shows the whole container, not the worker it landed on.

prometheus_client keeps one registry per process. Under gunicorn (N workers)
a scrape of /metrics reached one worker at random: counters jumped between
workers' values and ``rate()`` read the jumps as resets. gunicorn.conf.py sets
PROMETHEUS_MULTIPROC_DIR, and render_prometheus_exposition merges every
worker's files. Workers are separate processes, so the proof uses separate
processes too: two "workers" count, a third renders.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]

_WORKER = """
import sys
from app.observability.metrics import CREDIT_DEDUCTION_TOTAL, DB_POOL_ACTIVE, PG_LOCK_WAITERS
CREDIT_DEDUCTION_TOTAL.labels(outcome="ok").inc(int(sys.argv[1]))
DB_POOL_ACTIVE.set(int(sys.argv[2]))
PG_LOCK_WAITERS.set(int(sys.argv[3]))
"""

_RENDER = """
from app.observability.metrics import render_prometheus_exposition
body, ctype = render_prometheus_exposition()
print(body.decode())
"""


def _run(code: str, *args: str, mp_dir: Path, pid_env: dict | None = None) -> str:
    env = {**os.environ, "PROMETHEUS_MULTIPROC_DIR": str(mp_dir), "PYTHONDONTWRITEBYTECODE": "1"}
    out = subprocess.run([sys.executable, "-c", code, *args], cwd=str(BACKEND), env=env,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    return out.stdout


def _value(body: str, prefix: str) -> float:
    for line in body.splitlines():
        if line.startswith(prefix):
            return float(line.rsplit(" ", 1)[1])
    raise AssertionError(f"{prefix!r} not in exposition:\n{body[:3000]}")


def test_render_merges_every_worker_process(tmp_path):
    mp = tmp_path / "prom"
    mp.mkdir()
    _run(_WORKER, "2", "3", "1", mp_dir=mp)     # worker A: 2 deductions, 3 conns, sees 1 waiter
    _run(_WORKER, "5", "4", "1", mp_dir=mp)     # worker B: 5 deductions, 4 conns, sees 1 waiter
    body = _run(_RENDER, mp_dir=mp)
    assert _value(body, 'credit_deduction_total{outcome="ok"}') == 7.0   # summed, not one worker's
    assert 'pid="' not in body                                             # no per-process series
    # These are "live" gauges: the workers' processes have exited, so after
    # mark_process_dead they would drop out; before it, livesum sums them.
    assert _value(body, "db_pool_active ") == 7.0
    assert _value(body, "pg_lock_waiters ") == 1.0                         # same DB seen twice = 1


def test_every_gauge_declares_how_workers_combine():
    from prometheus_client import Gauge

    import app.observability.metrics as m

    undeclared = [
        name for name, obj in vars(m).items()
        if isinstance(obj, Gauge) and getattr(obj, "_multiprocess_mode", "all") in ("all", "liveall")
    ]
    assert undeclared == [], f"gauges that would carry a pid label per worker: {undeclared}"


def test_without_the_directory_it_is_this_process_registry(monkeypatch):
    monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR", raising=False)
    from app.observability.metrics import render_prometheus_exposition

    body, ctype = render_prometheus_exposition()
    assert b"http_request_duration_seconds" in body
    assert ctype.startswith("text/plain")


@pytest.mark.parametrize("mode_needed", ["livesum", "livemostrecent"])
def test_modes_used_are_supported_by_the_pinned_client(mode_needed):
    from prometheus_client.metrics import Gauge

    assert mode_needed in Gauge._MULTIPROC_MODES
