"""FIX133 A1 — worker and concurrency defaults computed from the machine.

Nothing here imports the app: ``backend/gunicorn.conf.py`` runs this before
the app exists, and the NER image's entrypoint runs the same formulas through
``python -m app.core.sizing``. Every number a pool is sized by is either read
from the container (cgroup v2 limits, /proc/meminfo, the CPU count) or given
in the environment; the defaults are the measured costs, not guesses:

* a backend (gunicorn/uvicorn) worker holds ~250 MB RSS under load (FIX131
  load runs: 1.07 GiB for 4 workers + master after 15 minutes at 10 users);
* a NER worker holds ~450 MB (1.46 GiB for 4 workers, live, idle);
* every backend worker may open 35 Postgres connections (sync 5+5, async
  10+15 — docker-compose.yml), and the server allows 250, of which 180 are
  the backend's budget (the rest: chat-edge, gateway, ops-watch, backup,
  psql sessions).

Formulas (all clamped to at least ``min_workers``):

    backend workers  = min(cpus // 3, ram / 250 MB, 180 / 35)      -> 4 on 12 vCPU
    NER workers      = min(cpus // 3, ram / 450 MB)                 -> 4 on 12 vCPU
    turns generating = max(4, cpus)                                 -> 12 on 12 vCPU

``cpus`` is the cgroup CPU quota when one is set (compose ``cpus:``), else the
CPU count; ``ram`` is the cgroup memory limit when one is set (compose
``mem_limit``), else half of the machine's memory, because the box is shared.
FIX131 measured 4 and 6 backend workers within noise of each other at 10
users (960 vs 950 turns in 15 min, p95 10.0 s both), so ``cpus // 3`` keeps
the default where the measurements are, and lets a bigger box scale.
"""

from __future__ import annotations

import os
import sys

MB = 1024 * 1024

BACKEND_WORKER_MB = 250
NER_WORKER_MB = 450
PG_BUDGET_CONNECTIONS = 180
PG_CONNECTIONS_PER_WORKER = 35


def _read(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return None


def effective_cpus() -> int:
    """CPUs this container may use: the cgroup v2 quota, else the count."""
    quota = _read("/sys/fs/cgroup/cpu.max")
    if quota:
        parts = quota.split()
        if len(parts) == 2 and parts[0] != "max":
            try:
                q, p = int(parts[0]), int(parts[1])
                if q > 0 and p > 0:
                    return max(1, -(-q // p))  # ceil
            except ValueError:
                pass
    try:
        n = len(os.sched_getaffinity(0))  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        n = os.cpu_count() or 1
    return max(1, n)


def machine_memory_bytes() -> int | None:
    info = _read("/proc/meminfo")
    if not info:
        return None
    for line in info.splitlines():
        if line.startswith("MemTotal:"):
            try:
                return int(line.split()[1]) * 1024
            except (IndexError, ValueError):
                return None
    return None


def memory_budget_bytes() -> int | None:
    """RAM a pool may plan with: the cgroup limit, else half the machine."""
    lim = _read("/sys/fs/cgroup/memory.max")
    if lim and lim != "max":
        try:
            return int(lim)
        except ValueError:
            pass
    total = machine_memory_bytes()
    return total // 2 if total else None


def _clamp(value: int, lo: int, hi: int | None) -> int:
    if hi is not None:
        value = min(value, hi)
    return max(lo, value)


def default_backend_workers(
    *,
    cpus: int | None = None,
    memory_bytes: int | None = None,
    per_worker_mb: int = BACKEND_WORKER_MB,
    pg_budget: int = PG_BUDGET_CONNECTIONS,
    pg_per_worker: int = PG_CONNECTIONS_PER_WORKER,
    min_workers: int = 2,
) -> int:
    cpus = cpus or effective_cpus()
    mem = memory_bytes if memory_bytes is not None else memory_budget_bytes()
    n = max(min_workers, cpus // 3)
    ram_cap = (mem // (per_worker_mb * MB)) if mem else None
    pg_cap = max(1, pg_budget // max(1, pg_per_worker))
    # RAM and the connection budget are hard caps: a box that cannot hold
    # two workers runs one, whatever the floor says.
    return max(1, _clamp(_clamp(n, 1, ram_cap), 1, pg_cap))


def default_ner_workers(
    *,
    cpus: int | None = None,
    memory_bytes: int | None = None,
    per_worker_mb: int = NER_WORKER_MB,
    min_workers: int = 2,
) -> int:
    cpus = cpus or effective_cpus()
    mem = memory_bytes if memory_bytes is not None else memory_budget_bytes()
    ram_cap = (mem // (per_worker_mb * MB)) if mem else None
    return max(1, _clamp(max(min_workers, cpus // 3), 1, ram_cap))


def default_max_active_turns(*, cpus: int | None = None) -> int:
    """Turns generating at once before the rest wait in the queue (A2).

    FIX131: ten non-stop users saturate this 12-vCPU box (p95 climbs after
    6-9 minutes, the CPU is the ceiling), so more concurrency than cores
    adds latency, not throughput.
    """
    cpus = cpus or effective_cpus()
    return max(4, cpus)


def env_int(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def main(argv: list[str]) -> int:
    """``python -m app.core.sizing <what>`` prints one number (entrypoints)."""
    what = argv[1] if len(argv) > 1 else "report"
    if what == "backend-workers":
        print(default_backend_workers())
    elif what == "ner-workers":
        print(default_ner_workers())
    elif what == "max-active-turns":
        print(default_max_active_turns())
    else:
        mem = memory_budget_bytes()
        print(
            f"cpus={effective_cpus()} memory_budget_mb={(mem or 0) // MB} "
            f"backend_workers={default_backend_workers()} ner_workers={default_ner_workers()} "
            f"max_active_turns={default_max_active_turns()}"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv))
