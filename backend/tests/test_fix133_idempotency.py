"""FIX133 F2 — the same request id sent twice creates one turn, one charge.

* ``Idempotency-Key`` on POST /v1/chat/completions (app.core.chat_idempotency):
  claim / in-progress / mismatch / replay / stream-done, and the route
  honouring it (the pipeline runs once).
* The media idempotency key binds the caller, not only a client-supplied
  tenant header.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest
from app.core import chat_idempotency as ci
from app.middleware.idempotency import cache_key_for


def _run(coro):
    return asyncio.run(coro)


def test_key_shape_and_redis_key():
    assert ci.ChatIdempotency.valid_key("turn-12345678") == "turn-12345678"
    assert ci.ChatIdempotency.valid_key("short") is None
    assert ci.ChatIdempotency.valid_key("has space 123456") is None
    assert ci.ChatIdempotency.valid_key(None) is None
    k1 = ci.ChatIdempotency.redis_key("t1", "key_a", "turn-12345678")
    k2 = ci.ChatIdempotency.redis_key("t1", "key_b", "turn-12345678")
    assert k1.startswith("idem:chat:t1:") and k1 != k2, "another API key is another caller"


def test_claim_states_in_order():
    async def run():
        idem = ci.ChatIdempotency(ci.MemoryIdempotencyStore())
        body = json.dumps({"model": "m", "messages": [{"role": "user", "content": "hi"}]})
        assert (await idem.claim("t1", "k1", "turn-aaaaaaaa", body)).state == "new"
        assert (await idem.claim("t1", "k1", "turn-aaaaaaaa", body)).state == "in_progress"
        assert (await idem.claim("t1", "k1", "turn-aaaaaaaa", body + " ")).state == "mismatch"
        await idem.complete("t1", "k1", "turn-aaaaaaaa", body, 200, {"id": "chatcmpl-1", "choices": []})
        replay = await idem.claim("t1", "k1", "turn-aaaaaaaa", body)
        assert replay.state == "replay" and replay.status == 200 and replay.body["id"] == "chatcmpl-1"
        # a streamed turn is remembered as done, never replayed or re-run
        assert (await idem.claim("t1", "k1", "turn-bbbbbbbb", body)).state == "new"
        await idem.stream_done("t1", "k1", "turn-bbbbbbbb", body)
        assert (await idem.claim("t1", "k1", "turn-bbbbbbbb", body)).state == "stream_done"
        # a released claim can run again (the first attempt failed)
        assert (await idem.claim("t1", "k1", "turn-cccccccc", body)).state == "new"
        await idem.release("t1", "k1", "turn-cccccccc")
        assert (await idem.claim("t1", "k1", "turn-cccccccc", body)).state == "new"
        # another caller with the same key is not the same request
        assert (await idem.claim("t1", "k2", "turn-aaaaaaaa", body)).state == "new"
    _run(run())


def test_a_broken_store_fails_open():
    class Broken:
        name = "broken"

        async def claim(self, key, record):
            raise ConnectionError("redis down")

        async def put(self, key, record):
            raise ConnectionError("redis down")

        async def delete(self, key):
            raise ConnectionError("redis down")

    async def run():
        idem = ci.ChatIdempotency(Broken())
        assert (await idem.claim("t", "k", "turn-aaaaaaaa", "{}")).state == "new"
        await idem.complete("t", "k", "turn-aaaaaaaa", "{}", 200, {})
        await idem.release("t", "k", "turn-aaaaaaaa")
    _run(run())


def test_media_cache_key_binds_the_caller():
    body = b'{"prompt":"a cat"}'
    a = cache_key_for("tenant-1", "Bearer gw_aaaa", "/v1/videos/generations", body)
    b = cache_key_for("tenant-1", "Bearer gw_bbbb", "/v1/videos/generations", body)
    same = cache_key_for("tenant-1", "Bearer gw_aaaa", "/v1/videos/generations", body)
    assert a == same and a != b
    assert "gw_aaaa" not in a, "the credential itself never appears in the key"


# ── the route: one pipeline run per key ───────────────────────────────────────


def test_chat_completions_honours_idempotency_key(monkeypatch):
    import httpx
    from fastapi import FastAPI
    from fastapi.responses import StreamingResponse

    from app.api import deps
    from app.api.routes import public_chat
    from app.core import turn_queue as tq
    from app.core.db import get_async_db
    from app.schemas.public import ChatCompletionsResponse
    from tests.test_fix133_turn_queue import _served

    ci.configure(ci.ChatIdempotency(ci.MemoryIdempotencyStore()))
    tq.reset_for_tests()
    tq.configure(tq.QueueConfig(enabled=False))
    try:
        app = FastAPI()
        app.include_router(public_chat.router, prefix="/v1")

        async def fake_auth():
            return deps.ResolvedApiKey(key_id="k1", key_prefix="gw_t", tenant_id="t1", project_id="p1", rate_limit_key_id="svc:u1")

        async def fake_db():
            yield None

        app.dependency_overrides[deps.get_resolved_api_key] = fake_auth
        app.dependency_overrides[get_async_db] = fake_db
        calls = {"n": 0}

        async def fake_impl(payload, response, auth, session, turn_id=None):
            calls["n"] += 1
            if payload.stream:
                async def gen():
                    yield "data: " + json.dumps({"choices": [{"delta": {"content": "hi"}}]}) + "\n\n"
                    yield "data: [DONE]\n\n"
                return StreamingResponse(gen(), media_type="text/event-stream")
            return ChatCompletionsResponse.model_validate({
                "id": f"chatcmpl-{calls['n']}", "object": "chat.completion", "created": int(time.time()),
                "model": "m", "provider": "mock", "choices": [{"index": 0, "message": {"role": "assistant", "content": "hello"},
                                          "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            })

        monkeypatch.setattr(public_chat, "_chat_completions_impl", fake_impl)

        async def no_row(**kw):
            return False

        monkeypatch.setattr(public_chat.pre_provider_log, "record_failure", no_row)
        body = {"model": "m", "messages": [{"role": "user", "content": "hi"}], "stream": False}
        with _served(app) as base, httpx.Client(timeout=30) as c:
            r1 = c.post(base + "/v1/chat/completions", json=body, headers={"Idempotency-Key": "agent-run-0001"})
            assert r1.status_code == 200 and r1.headers.get("idempotent-replayed") is None
            r2 = c.post(base + "/v1/chat/completions", json=body, headers={"Idempotency-Key": "agent-run-0001"})
            assert r2.status_code == 200 and r2.headers.get("idempotent-replayed") == "true"
            assert r2.json()["id"] == r1.json()["id"]
            assert calls["n"] == 1, "one turn, one charge"
            r3 = c.post(base + "/v1/chat/completions", json={**body, "messages": [{"role": "user", "content": "other"}]},
                        headers={"Idempotency-Key": "agent-run-0001"})
            assert r3.status_code == 422 and r3.json()["error"]["code"] == "idempotency_key_reused"
            # no key: every call is a new turn (unchanged behaviour)
            c.post(base + "/v1/chat/completions", json=body)
            c.post(base + "/v1/chat/completions", json=body)
            assert calls["n"] == 3
            # a streamed turn is never generated twice
            sbody = {**body, "stream": True}
            with c.stream("POST", base + "/v1/chat/completions", json=sbody, headers={"Idempotency-Key": "agent-run-0002"}) as s1:
                frames = [ln for ln in s1.iter_lines() if ln.startswith("data: ")]
            assert frames[-1] == "data: [DONE]" and calls["n"] == 4
            r5 = c.post(base + "/v1/chat/completions", json=sbody, headers={"Idempotency-Key": "agent-run-0002"})
            assert r5.status_code == 409 and r5.json()["error"]["code"] == "idempotent_stream_done"
            assert calls["n"] == 4
    finally:
        ci.configure(None)
        tq.reset_for_tests()
