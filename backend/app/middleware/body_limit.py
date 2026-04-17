"""Request body size limit middleware — lightweight ASGI implementation.

Rejects requests with Content-Length exceeding the configured limit.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.responses import JSONResponse

DEFAULT_MAX_BODY_BYTES = 1 * 1024 * 1024  # 1 MB


class BodySizeLimitMiddleware:

    def __init__(self, app: ASGIApp, max_bytes: int = DEFAULT_MAX_BODY_BYTES) -> None:
        self.app = app
        self._max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        content_length = headers.get(b"content-length", b"0")
        try:
            length = int(content_length)
        except (ValueError, TypeError):
            length = 0

        if length > self._max_bytes:
            response = JSONResponse(
                status_code=413,
                content={
                    "error": {
                        "code": "request_too_large",
                        "message": f"Request body exceeds {self._max_bytes} byte limit.",
                    },
                },
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
