"""FIX129 A3 — when did this worker last complete a turn?

``/ready`` proves the engines answer; it cannot prove a turn completes.
``request_log_queue.enqueue`` calls ``mark`` for every request record it
sees, so ``/ready`` can report the age of the last success and the last
attempt in this worker without a database query. Process-local, like the
in-flight registry.

FIX131 — under gunicorn the backend is N such workers; ``timestamps()`` is
what each one publishes on the worker board (app/core/worker_board.py) and
``snapshot(peers)`` answers for the whole container: the most recent success
and attempt of ANY worker, whichever worker /ready landed on.
"""

from __future__ import annotations

import time

_last_success: float | None = None
_last_attempt: float | None = None


def mark(status: str) -> None:
    global _last_success, _last_attempt
    now = time.time()
    _last_attempt = now
    if status == "success":
        _last_success = now


def timestamps() -> dict:
    """This worker's raw epoch timestamps (None = never), for the worker board."""
    return {"last_success_at": _last_success, "last_attempt_at": _last_attempt}


def _latest(own: float | None, peers: list[dict] | None, key: str) -> float | None:
    values = [own] + [p.get(key) for p in (peers or [])]
    nums = [float(v) for v in values if isinstance(v, (int, float))]
    return max(nums) if nums else None


def snapshot(peers: list[dict] | None = None) -> dict:
    """Ages of the last success / attempt, over this worker and ``peers``."""
    now = time.time()
    success = _latest(_last_success, peers, "last_success_at")
    attempt = _latest(_last_attempt, peers, "last_attempt_at")
    return {
        "last_success_age_s": round(now - success, 1) if success is not None else None,
        "last_attempt_age_s": round(now - attempt, 1) if attempt is not None else None,
    }


def reset() -> None:  # tests
    global _last_success, _last_attempt
    _last_success = None
    _last_attempt = None
