"""Request timeout middleware — lightweight ASGI implementation.

Unlike BaseHTTPMiddleware, this doesn't buffer the response body,
avoiding the worker-starvation issue.
"""

from __future__ import annotations

import asyncio
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.responses import JSONResponse


class TimeoutMiddleware:

    def __init__(self, app: ASGIApp, timeout_seconds: float = 20.0) -> None:
        self.app = app
        self._timeout = timeout_seconds

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Skip timeout for streaming endpoints (SSE responses may run long)
        path = scope.get("path", "")
        if path == "/v1/chat/completions":
            await self.app(scope, receive, send)
            return

        try:
            await asyncio.wait_for(
                self.app(scope, receive, send),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            response = JSONResponse(
                status_code=504,
                content={
                    "error": {
                        "code": "timeout",
                        "message": f"Request timed out after {self._timeout}s",
                    },
                },
            )
            await response(scope, receive, send)
