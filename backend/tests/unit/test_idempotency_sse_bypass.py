"""Tests for the SSE bypass in IdempotencyMiddleware (Sprint 2A).

BaseHTTPMiddleware buffers response bodies, which breaks Server-Sent
Events. The middleware must skip SSE traffic entirely — even on an
allowlisted path — while still caching ordinary allowlisted requests.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.testclient import TestClient

from app.middleware.idempotency import IdempotencyMiddleware


class FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value


def _build_app(fake: FakeRedis):
    app = FastAPI()
    counters = {"n": 0}
    app.add_middleware(
        IdempotencyMiddleware,
        allowlist=("/v1/videos/",),
        redis_factory=lambda: fake,
    )

    @app.post("/v1/videos/generations")
    async def gen(request: Request):
        counters["n"] += 1
        await request.body()
        return JSONResponse({"n": counters["n"]})

    @app.post("/v1/conversations/{cid}/messages")
    async def msg(request: Request, cid: str):
        counters["n"] += 1
        return JSONResponse({"n": counters["n"]})

    return app, counters


def test_event_stream_accept_bypasses_cache_on_allowlisted_route():
    fake = FakeRedis()
    app, counters = _build_app(fake)
    client = TestClient(app)
    headers = {"X-Idempotency-Key": "k1", "Accept": "text/event-stream"}
    client.post("/v1/videos/generations", json={"p": "a"}, headers=headers)
    client.post("/v1/videos/generations", json={"p": "a"}, headers=headers)
    # SSE bypass wins over the allowlist → no caching, handler runs both times.
    assert counters["n"] == 2
    assert fake.store == {}


def test_sse_prefix_path_not_cached():
    fake = FakeRedis()
    app, counters = _build_app(fake)
    client = TestClient(app)
    headers = {"X-Idempotency-Key": "k1"}
    client.post("/v1/conversations/abc/messages", json={"p": "a"}, headers=headers)
    client.post("/v1/conversations/abc/messages", json={"p": "a"}, headers=headers)
    assert counters["n"] == 2
    assert fake.store == {}


def test_allowlisted_non_sse_still_caches():
    """Regression: ordinary allowlisted requests still dedupe."""
    fake = FakeRedis()
    app, counters = _build_app(fake)
    client = TestClient(app)
    headers = {"X-Idempotency-Key": "k1"}
    r1 = client.post("/v1/videos/generations", json={"p": "a"}, headers=headers)
    r2 = client.post("/v1/videos/generations", json={"p": "a"}, headers=headers)
    assert counters["n"] == 1
    assert r1.json() == r2.json()
