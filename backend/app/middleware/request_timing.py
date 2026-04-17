"""Request timing middleware — debug-grade START/END logging with timing.

Lightweight ASGI middleware (no BaseHTTPMiddleware) so it doesn't buffer
the response body. Logs every request's START and END so we can detect
a request that enters the handler but never emits a response.
"""

from __future__ import annotations

import logging
import time
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("request_timing")


class RequestTimingMiddleware:

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "?")
        path = scope.get("path", "?")
        start = time.monotonic()
        status_code = {"code": 0}

        logger.info(f"-> {method} {path} START")

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_code["code"] = message.get("status", 0)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as e:
            ms = (time.monotonic() - start) * 1000
            logger.error(f"x {method} {path} ERROR after {ms:.0f}ms: {e}")
            raise
        else:
            ms = (time.monotonic() - start) * 1000
            logger.info(f"<- {method} {path} {status_code['code']} {ms:.0f}ms")
            if ms > 2000:
                logger.warning(f"SLOW {method} {path} took {ms:.0f}ms")
