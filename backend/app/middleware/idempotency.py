"""Idempotency middleware for media-generation submit routes (Sprint 1).

Reads an ``X-Idempotency-Key`` header. When present AND the request path
is allowlisted (default ``/v1/videos/``, ``/v1/audio/``), the middleware
serves a cached response for a repeated request so an accidental
double-submit doesn't enqueue (and bill) two generation jobs.

Cache key = ``sha256(tenant_id | route | body)`` (the request body is the
dedup surface, per the Sprint plan), namespaced ``idem:{tenant}:{digest}``
in Redis with a 24h TTL. A different body yields a different key — it is
treated as a distinct request, not a replay.

Behavior contract:
- No ``X-Idempotency-Key`` header → pass through untouched (no cache).
- Header present but path NOT allowlisted → pass through untouched.
- Header + allowlisted + cache HIT → return the cached response; the
  downstream handler is NOT invoked.
- Header + allowlisted + cache MISS → execute, cache a 2xx response, return.
- Redis unavailable → fail-open (execute normally, no caching).

NOTE (tech debt): this uses ``BaseHTTPMiddleware``, which this codebase
otherwise avoids on the hot path (see main.py middleware comment). It is
acceptable here because it is gated on/off via the ``IDEMPOTENCY_ENABLED``
env var and only does real work on the two low-volume media submit routes;
a pure-ASGI rewrite is deferred to a later sprint.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Callable, Iterable, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

log = logging.getLogger(__name__)

_DEFAULT_ALLOWLIST = ("/v1/videos/", "/v1/audio/")
_DEFAULT_TTL_SECONDS = 24 * 60 * 60
_HEADER = "x-idempotency-key"
_TENANT_HEADER = "x-tenant-id"


class IdempotencyMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        *,
        allowlist: Optional[Iterable[str]] = None,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
        redis_factory: Optional[Callable[[], object]] = None,
    ) -> None:
        super().__init__(app)
        self._allowlist = (
            tuple(allowlist) if allowlist is not None else _DEFAULT_ALLOWLIST
        )
        self._ttl = int(ttl_seconds)
        if redis_factory is None:
            from app.services._redis_pool import get_async_redis

            redis_factory = get_async_redis
        self._redis_factory = redis_factory

    def _is_allowlisted(self, path: str) -> bool:
        return any(path.startswith(prefix) for prefix in self._allowlist)

    async def dispatch(self, request: Request, call_next):
        key = request.headers.get(_HEADER)
        if not key or not self._is_allowlisted(request.url.path):
            return await call_next(request)

        body = await request.body()

        # Replay the consumed body so the downstream handler can read it.
        async def _receive():
            return {"type": "http.request", "body": body, "more_body": False}

        request._receive = _receive  # type: ignore[attr-defined]

        tenant_id = request.headers.get(_TENANT_HEADER, "")
        digest = hashlib.sha256(
            tenant_id.encode()
            + b"|"
            + request.url.path.encode()
            + b"|"
            + body
        ).hexdigest()
        cache_key = f"idem:{tenant_id}:{digest}"

        redis = None
        cached = None
        try:
            redis = self._redis_factory()
            cached = await redis.get(cache_key)
        except Exception:  # noqa: BLE001 — fail-open on cache outage
            log.warning("idempotency cache read failed; proceeding", exc_info=True)
            redis = None

        if cached is not None:
            try:
                data = json.loads(cached)
                return Response(
                    content=bytes.fromhex(data["body"]),
                    status_code=int(data["status"]),
                    media_type=data.get("media_type"),
                    headers=data.get("headers") or {},
                )
            except Exception:  # noqa: BLE001 — corrupt entry → re-execute
                log.warning(
                    "idempotency cache decode failed; re-executing", exc_info=True
                )

        response = await call_next(request)

        # Buffer the streamed response so it can be both cached and returned.
        chunks = [chunk async for chunk in response.body_iterator]
        body_bytes = b"".join(
            c if isinstance(c, bytes) else c.encode() for c in chunks
        )

        if redis is not None and 200 <= response.status_code < 300:
            content_type = response.headers.get("content-type")
            payload = {
                "status": response.status_code,
                "body": body_bytes.hex(),
                "media_type": response.media_type,
                "headers": {"content-type": content_type} if content_type else {},
            }
            try:
                await redis.set(cache_key, json.dumps(payload), ex=self._ttl)
            except Exception:  # noqa: BLE001 — cache write best-effort
                log.warning("idempotency cache write failed", exc_info=True)

        # Rebuild a fresh response (Content-Length recomputed from the body).
        headers = {
            k: v
            for k, v in response.headers.items()
            if k.lower() != "content-length"
        }
        return Response(
            content=body_bytes,
            status_code=response.status_code,
            headers=headers,
            media_type=response.media_type,
        )
