"""Simple in-memory request metrics — lightweight ASGI implementation."""

from __future__ import annotations

import time
from collections import defaultdict

from starlette.types import ASGIApp, Receive, Scope, Send


_start_time = time.time()
_request_counts: dict[str, int] = defaultdict(int)
_status_counts: dict[int, int] = defaultdict(int)
_error_counts: dict[str, int] = defaultdict(int)


def get_metrics() -> dict:
    return {
        "uptime_seconds": round(time.time() - _start_time),
        "total_requests": sum(_request_counts.values()),
        "total_errors": sum(_error_counts.values()),
        "requests_by_path": dict(_request_counts),
        "requests_by_status": dict(_status_counts),
        "errors_by_path": dict(_error_counts),
    }


class MetricsMiddleware:

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        _request_counts[path] += 1

        status_code = 200

        async def send_wrapper(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 200)
            await send(message)

        await self.app(scope, receive, send_wrapper)

        _status_counts[status_code] += 1
        if status_code >= 500:
            _error_counts[path] += 1
