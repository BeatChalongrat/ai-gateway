"""Tests for IdempotencyMiddleware (Sprint 1).

Drives a tiny FastAPI app with a counting handler under an allowlisted
route and a fake async Redis, asserting the cache contract.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.testclient import TestClient

from app.middleware.idempotency import IdempotencyMiddleware


class FakeRedis:
    """Minimal async Redis stand-in supporting get/set with TTL ignore."""

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
        payload = await request.json()
        return JSONResponse({"n": counters["n"], "echo": payload.get("p")})

    @app.post("/other")
    async def other(request: Request):
        counters["n"] += 1
        return JSONResponse({"n": counters["n"]})

    return app, counters


def test_no_header_passes_through():
    fake = FakeRedis()
    app, counters = _build_app(fake)
    client = TestClient(app)
    r = client.post("/v1/videos/generations", json={"p": "a"})
    assert r.status_code == 200
    assert counters["n"] == 1
    assert fake.store == {}  # nothing cached without the header


def test_non_allowlisted_route_not_cached():
    fake = FakeRedis()
    app, counters = _build_app(fake)
    client = TestClient(app)
    headers = {"X-Idempotency-Key": "k1"}
    client.post("/other", json={"p": "a"}, headers=headers)
    client.post("/other", json={"p": "a"}, headers=headers)
    assert counters["n"] == 2  # handler ran both times
    assert fake.store == {}


def test_identical_request_served_from_cache():
    fake = FakeRedis()
    app, counters = _build_app(fake)
    client = TestClient(app)
    headers = {"X-Idempotency-Key": "k1"}
    r1 = client.post("/v1/videos/generations", json={"p": "a"}, headers=headers)
    r2 = client.post("/v1/videos/generations", json={"p": "a"}, headers=headers)
    assert counters["n"] == 1  # handler invoked ONCE
    assert r1.json() == r2.json()
    assert r2.json()["n"] == 1


def test_different_body_is_distinct_key():
    """Cache key = hash(tenant|route|body); a different body misses the
    cache and re-executes (the plan's dedup surface is the body)."""
    fake = FakeRedis()
    app, counters = _build_app(fake)
    client = TestClient(app)
    headers = {"X-Idempotency-Key": "k1"}
    client.post("/v1/videos/generations", json={"p": "a"}, headers=headers)
    client.post("/v1/videos/generations", json={"p": "b"}, headers=headers)
    assert counters["n"] == 2  # different body → different key → re-executed


def test_ttl_expiry_re_executes():
    fake = FakeRedis()
    app, counters = _build_app(fake)
    client = TestClient(app)
    headers = {"X-Idempotency-Key": "k1"}
    client.post("/v1/videos/generations", json={"p": "a"}, headers=headers)
    assert counters["n"] == 1
    fake.store.clear()  # simulate TTL expiry
    client.post("/v1/videos/generations", json={"p": "a"}, headers=headers)
    assert counters["n"] == 2
