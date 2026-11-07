"""Request body size limit middleware — lightweight ASGI implementation.

Rejects requests with Content-Length exceeding the configured limit.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.responses import JSONResponse

DEFAULT_MAX_BODY_BYTES = 1 * 1024 * 1024  # 1 MB


class BodySizeLimitMiddleware:

    def __init__(
        self,
        app: ASGIApp,
        max_bytes: int = DEFAULT_MAX_BODY_BYTES,
        path_overrides: dict[str, int] | None = None,
    ) -> None:
        self.app = app
        self._max_bytes = max_bytes
        # Prefix-matched overrides — e.g. avatar upload needs 5 MB while
        # everything else stays at 1 MB. Longest prefix wins so that
        # "/portal/me/avatar" can differ from "/portal/me".
        self._path_overrides = sorted(
            (path_overrides or {}).items(), key=lambda kv: len(kv[0]), reverse=True
        )

    def _limit_for(self, path: str) -> int:
        for prefix, limit in self._path_overrides:
            if path.startswith(prefix):
                return limit
        return self._max_bytes

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

        limit = self._limit_for(scope.get("path", ""))
        if length > limit:
            response = JSONResponse(
                status_code=413,
                content={
                    "error": {
                        "code": "request_too_large",
                        "message": f"Request body exceeds {limit} byte limit.",
                    },
                },
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
