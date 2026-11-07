"""FIX133 A1 — NER worker count from the machine (the image's CMD runs this
when NER_WORKERS is empty). Same formula as backend/app/core/sizing.py:
min(cpus // 3, RAM / 1100 MB per worker), at least 2 — each worker loads its
own spaCy + Presidio analyzer (1.0-1.15 GB RSS each under load, FIX133 runs).
The cgroup limits (compose ``cpus:`` / ``mem_limit``) win over the machine.
"""

from __future__ import annotations

import os

MB = 1024 * 1024
WORKER_MB = 1100


def _read(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return None


def cpus() -> int:
    quota = _read("/sys/fs/cgroup/cpu.max")
    if quota:
        parts = quota.split()
        if len(parts) == 2 and parts[0] != "max":
            try:
                q, p = int(parts[0]), int(parts[1])
                if q > 0 and p > 0:
                    return max(1, -(-q // p))
            except ValueError:
                pass
    try:
        return max(1, len(os.sched_getaffinity(0)))  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return max(1, os.cpu_count() or 1)


def memory_budget() -> int | None:
    lim = _read("/sys/fs/cgroup/memory.max")
    if lim and lim != "max":
        try:
            return int(lim)
        except ValueError:
            pass
    info = _read("/proc/meminfo") or ""
    for line in info.splitlines():
        if line.startswith("MemTotal:"):
            try:
                return int(line.split()[1]) * 1024 // 2
            except (IndexError, ValueError):
                return None
    return None


def default_workers(*, cpu_count: int | None = None, memory_bytes: int | None = None, min_workers: int = 2) -> int:
    n = max(min_workers, (cpu_count or cpus()) // 3)
    mem = memory_bytes if memory_bytes is not None else memory_budget()
    if mem:
        n = min(n, max(1, mem // (WORKER_MB * MB)))
    return max(1, n)


if __name__ == "__main__":
    raw = (os.environ.get("NER_WORKERS") or "").strip()
    print(int(raw) if raw.isdigit() and int(raw) > 0 else default_workers())
