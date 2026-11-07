"""Short-lived in-memory store for Admin Secure Trace.

This deliberately does not persist raw trace data. Normal request logs stay
masked/placeholder-only; this buffer exists only so a privileged admin can
debug the most recent request while it is still in the current backend
process.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from threading import RLock


@dataclass(frozen=True)
class SecureTraceRecord:
    request_id: str
    tenant_id: str
    project_id: str
    user_to_service: str | None
    service_to_ai: str | None
    ai_to_service: str | None
    service_to_user: str | None
    policy_action: str
    findings_count: int
    detectors: list[str]
    created_at: float


class SecureTraceStore:
    def __init__(self, ttl_seconds: int = 900, max_entries: int = 512) -> None:
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._records: dict[str, SecureTraceRecord] = {}
        self._lock = RLock()

    def put(self, record: SecureTraceRecord) -> None:
        with self._lock:
            self._prune_locked()
            self._records[record.request_id] = record
            if len(self._records) > self._max_entries:
                oldest = sorted(
                    self._records.values(),
                    key=lambda item: item.created_at,
                )
                for item in oldest[: len(self._records) - self._max_entries]:
                    self._records.pop(item.request_id, None)

    def get(self, request_id: str) -> SecureTraceRecord | None:
        with self._lock:
            self._prune_locked()
            return self._records.get(request_id)

    def clear(self) -> None:
        with self._lock:
            self._records.clear()

    def _prune_locked(self) -> None:
        cutoff = time.time() - self._ttl_seconds
        expired = [
            request_id
            for request_id, record in self._records.items()
            if record.created_at < cutoff
        ]
        for request_id in expired:
            self._records.pop(request_id, None)


secure_trace_store = SecureTraceStore()
