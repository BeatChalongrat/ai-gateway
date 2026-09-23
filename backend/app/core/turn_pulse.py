"""FIX129 A3 — when did this worker last complete a turn?

``/ready`` proves the engines answer; it cannot prove a turn completes.
``request_log_queue.enqueue`` calls ``mark`` for every request record it
sees, so ``/ready`` can report the age of the last success and the last
attempt in this worker without a database query. Process-local, like the
in-flight registry.
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


def snapshot() -> dict:
    now = time.time()
    return {
        "last_success_age_s": (
            round(now - _last_success, 1) if _last_success is not None else None
        ),
        "last_attempt_age_s": (
            round(now - _last_attempt, 1) if _last_attempt is not None else None
        ),
    }


def reset() -> None:  # tests
    global _last_success, _last_attempt
    _last_success = None
    _last_attempt = None
