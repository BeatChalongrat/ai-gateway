"""Request timeout middleware — lightweight ASGI implementation.

Unlike BaseHTTPMiddleware, this doesn't buffer the response body,
avoiding the worker-starvation issue.
"""

from __future__ import annotations

import asyncio
import structlog
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.responses import JSONResponse

logger = structlog.get_logger(__name__)

class TimeoutMiddleware:

    def __init__(
        self,
        app: ASGIApp,
        timeout_seconds: float = 20.0,
        path_timeouts: dict[str, float] | None = None,
    ) -> None:
        self.app = app
        self._timeout = timeout_seconds
        self._path_timeouts = path_timeouts or {}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Skip timeout for streaming endpoints (SSE responses may run long)
        path = scope.get("path", "")
        if path == "/v1/chat/completions":
            await self.app(scope, receive, send)
            return
        timeout = self._path_timeouts.get(path, self._timeout)

        try:
            await asyncio.wait_for(
                self.app(scope, receive, send),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            headers = {
                key.decode("latin1").lower(): value.decode("latin1")
                for key, value in scope.get("headers", [])
            }
            logger.warning(
                "request_timeout",
                request_id=headers.get("x-request-id"),
                path=path,
                method=scope.get("method"),
                timeout_ms=int(timeout * 1000),
            )
            response = JSONResponse(
                status_code=504,
                content={
                    "error": {
                        "code": "timeout",
                        "message": f"Request timed out after {timeout}s",
                    },
                },
            )
            await response(scope, receive, send)
