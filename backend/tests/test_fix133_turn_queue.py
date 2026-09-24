"""FIX133 A1/A2/A3 — the turn queue, its sizing, and the chat route's
early-open path.

Pure and monkeypatched: the memory store drives the admission algorithm;
the Redis script is proven against the memory store in
``test_redis_store_matches_memory_store`` when SRAN_TEST_REDIS_URL names a
THROWAWAY Redis and SRAN_ALLOW_REAL_REDIS_IN_TESTS=1 lifts the FIX124
isolation (the lane sets neither: it runs with no network).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import socket
import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from app.core import sizing
from app.core import turn_queue as tq
from app.core.turn_queue import (
    MemoryTurnQueueStore,
    QueueConfig,
    QueueFull,
    QueueTimeout,
    TurnQueue,
)


def _run(coro):
    return asyncio.run(coro)


# ── A1: sizing from the machine, never a constant ────────────────────────────


def test_backend_workers_follow_cores_ram_and_the_pg_budget():
    gib = 1024**3
    # 12 vCPU / 8 GiB budget (half of 16): cores/3 = 4, RAM allows 32, PG allows 5 → 4
    assert sizing.default_backend_workers(cpus=12, memory_bytes=8 * gib) == 4
    # 48 vCPU: cores say 16, the Postgres budget (180 / 35) caps at 5
    assert sizing.default_backend_workers(cpus=48, memory_bytes=64 * gib) == 5
    # A 1 GiB container: RAM caps at 4 (250 MB each) — cores would say 8
    assert sizing.default_backend_workers(cpus=24, memory_bytes=1 * gib) == 4
    # A tiny box never goes below 2 workers (one can wedge) …
    assert sizing.default_backend_workers(cpus=2, memory_bytes=8 * gib) == 2
    # … unless the RAM cannot hold two
    assert sizing.default_backend_workers(cpus=1, memory_bytes=256 * 1024**2) == 1


def test_ner_workers_and_active_turns_follow_the_machine():
    gib = 1024**3
    assert sizing.default_ner_workers(cpus=12, memory_bytes=7 * gib) == 4  # 7 g / 450 MB = 15, cores/3 = 4
    assert sizing.default_ner_workers(cpus=12, memory_bytes=1 * gib) == 2  # RAM: 2
    assert sizing.default_max_active_turns(cpus=12) == 12
    assert sizing.default_max_active_turns(cpus=2) == 4


def test_gunicorn_config_reads_workers_from_the_environment(monkeypatch):
    """WORKERS in .env wins; GUNICORN_WORKERS still works; empty = formula."""
    src = open(os.path.join(os.path.dirname(__file__), "..", "gunicorn.conf.py"), encoding="utf-8").read()
    monkeypatch.setenv("SRAN_RUNTIME_DIR", "/tmp/sran-backend-test-fix133")

    def load() -> int:
        ns: dict = {"__file__": os.path.join(os.path.dirname(__file__), "..", "gunicorn.conf.py")}
        exec(compile(src, "gunicorn.conf.py", "exec"), ns)
        return ns["workers"]

    monkeypatch.setenv("WORKERS", "7")
    monkeypatch.delenv("GUNICORN_WORKERS", raising=False)
    assert load() == 7
    monkeypatch.setenv("WORKERS", "")
    monkeypatch.setenv("GUNICORN_WORKERS", "3")
    assert load() == 3
    monkeypatch.setenv("GUNICORN_WORKERS", "")
    assert load() == sizing.default_backend_workers()


# ── A2/A3: the queue ─────────────────────────────────────────────────────────


def _queue(**kw) -> TurnQueue:
    cfg = QueueConfig(max_active=kw.pop("max_active", 2), max_per_identity=kw.pop("max_per_identity", 1),
                      max_waiting=kw.pop("max_waiting", 4), max_wait_s=kw.pop("max_wait_s", 2.0),
                      poll_s=kw.pop("poll_s", 0.02), frame_every_s=kw.pop("frame_every_s", 60.0), **kw)
    return TurnQueue(MemoryTurnQueueStore(), cfg)


def test_resolved_defaults_follow_max_active():
    cfg = QueueConfig(max_active=12).resolved()
    assert (cfg.max_active, cfg.max_per_identity, cfg.max_waiting) == (12, 2, 48)
    cfg = QueueConfig(max_active=4).resolved()
    assert (cfg.max_per_identity, cfg.max_waiting) == (1, 16)


def test_slots_are_granted_at_once_while_free_then_the_line_forms():
    async def run():
        q = _queue(max_active=2, max_per_identity=2)
        t1, s1 = await q.try_admit("u1", None)
        t2, s2 = await q.try_admit("u2", None)
        assert s1.granted and s2.granted and s1.position == 0
        t3, s3 = await q.try_admit("u3", None)
        assert not s3.granted and s3.position == 1 and s3.ahead == 0 and s3.active == 2
        t4, s4 = await q.try_admit("u4", None)
        assert s4.position == 2 and s4.ahead == 1 and s4.waiting == 2
        snap = await q.snapshot()
        assert (snap.active, snap.waiting, snap.max_active) == (2, 2, 2)
        # the first slot frees → the head of the line gets it, not the tail
        await t1.release()
        seen = []
        st = await q.wait(t3, lambda s: seen.append(s))
        assert st.granted and seen[-1].position == 0
        _, s4b = await q.try_admit("u4b", None)  # a fresh caller lands behind t4
        assert s4b.position == 2
        await t2.release(); await t3.release(); await t4.release()
    _run(run())


def test_one_identity_cannot_take_every_slot():
    async def run():
        q = _queue(max_active=2, max_per_identity=1)
        ta, sa = await q.try_admit("agent", None)
        assert sa.granted
        tb, sb = await q.try_admit("agent", None)  # at its share: waits although a slot is free
        assert not sb.granted and sb.position == 1 and sb.active == 1
        th, sh = await q.try_admit("human", None)  # the free slot goes to someone else
        assert sh.granted
        await ta.release()
        st = await q.wait(tb)
        assert st.granted
        await tb.release(); await th.release()
    _run(run())


def test_the_line_is_refused_only_when_too_long():
    async def run():
        q = _queue(max_active=1, max_per_identity=1, max_waiting=2)
        t1, _ = await q.try_admit("a", None)
        await q.try_admit("b", None)
        await q.try_admit("c", None)
        with pytest.raises(QueueFull) as ei:
            await q.try_admit("d", None)
        assert ei.value.waiting == 2 and ei.value.retry_after_s > 0
        await t1.release()
    _run(run())


def test_a_wait_ends_as_queue_timeout_and_leaves_the_line():
    async def run():
        q = _queue(max_active=1, max_per_identity=1, max_wait_s=0.1)
        t1, _ = await q.try_admit("a", None)
        t2, s2 = await q.try_admit("b", None)
        assert not s2.granted
        with pytest.raises(QueueTimeout):
            await q.wait(t2)
        snap = await q.snapshot()
        assert snap.waiting == 0, "a timed-out ticket must not keep its place"
        await t1.release()
    _run(run())


def test_a_cancelled_wait_leaves_the_line():
    async def run():
        q = _queue(max_active=1, max_per_identity=1, max_wait_s=5)
        t1, _ = await q.try_admit("a", None)
        t2, _ = await q.try_admit("b", None)
        task = asyncio.create_task(q.wait(t2))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert (await q.snapshot()).waiting == 0
        await t1.release()
    _run(run())


def test_a_follow_up_of_an_admitted_turn_never_waits_again():
    async def run():
        q = _queue(max_active=1, max_per_identity=1)
        t1, s1 = await q.try_admit("a", "turn-aaaaaaaa")
        assert s1.granted
        await t1.release()  # the tool round ended; the model call comes back
        t_other, s_other = await q.try_admit("b", None)
        assert s_other.granted  # the slot went to someone else meanwhile
        t1b, s1b = await q.try_admit("a", "turn-aaaaaaaa")  # same turn: straight in
        assert s1b.granted
        await t1b.release(); await t_other.release()
    _run(run())


def test_status_reports_place_changes_and_an_estimate_once_known():
    async def run():
        q = _queue(max_active=1, max_per_identity=1)
        t1, _ = await q.try_admit("a", None)
        await asyncio.sleep(0.03)
        await t1.release()  # records a duration → estimates exist from now on
        t2, _ = await q.try_admit("b", None)
        t3, s3 = await q.try_admit("c", None)
        assert s3.position == 1 and s3.estimate_s is not None and s3.estimate_s >= 0
        frame = s3.frame()
        assert frame["type"] == "queued" and frame["position"] == 1 and "estimate_s" in frame
        await t2.release()
        st = await q.wait(t3)
        assert st.granted and st.frame()["position"] == 0
        await t3.release()
    _run(run())


def test_a_dead_holder_frees_its_slot_after_the_ttl():
    async def run():
        clock = [1000.0]
        cfg = QueueConfig(max_active=1, max_per_identity=1, max_waiting=4, max_wait_s=2, poll_s=0.01, active_ttl_s=1.0)
        q = TurnQueue(MemoryTurnQueueStore(), cfg, clock=lambda: clock[0])
        t1, s1 = await q.try_admit("a", None)
        assert s1.granted
        q._held.clear()  # the worker holding t1 died: nobody renews it
        t2, s2 = await q.try_admit("b", None)
        assert not s2.granted
        clock[0] += 1.5  # past the TTL
        st = await q.wait(t2)
        assert st.granted
        await t2.release()
    _run(run())


def test_purposes_that_never_queue():
    assert tq.purpose_never_queues({"purpose": "title"})
    assert tq.purpose_never_queues({"purpose": "Memory_Extraction"})
    assert not tq.purpose_never_queues({"purpose": "chat"})
    assert not tq.purpose_never_queues(None)


@pytest.mark.skipif(
    not (os.environ.get("SRAN_TEST_REDIS_URL") and os.environ.get("SRAN_ALLOW_REAL_REDIS_IN_TESTS") == "1"),
    reason="needs a THROWAWAY Redis: SRAN_TEST_REDIS_URL + SRAN_ALLOW_REAL_REDIS_IN_TESTS=1 (never the lane)",
)
def test_redis_store_matches_memory_store():
    """The Lua script and the Python store make the same decisions."""
    import redis.asyncio as aioredis

    async def scenario(store):
        q = TurnQueue(store, QueueConfig(max_active=2, max_per_identity=1, max_waiting=3, max_wait_s=1, poll_s=0.01))
        out = []
        ta, sa = await q.try_admit("a", None); out.append((sa.granted, sa.position))
        tb, sb = await q.try_admit("a", None); out.append((sb.granted, sb.position))  # a's second: waits
        tc, sc = await q.try_admit("c", None); out.append((sc.granted, sc.position))  # c takes the free slot
        td, sd = await q.try_admit("d", None); out.append((sd.granted, sd.position))
        te, se = await q.try_admit("e", None); out.append((se.granted, se.position))
        try:
            await q.try_admit("f", None); out.append("admitted")
        except QueueFull:
            out.append("full")
        await ta.release()
        st = await q.wait(tb); out.append((st.granted, st.position))
        snap = await q.snapshot(); out.append((snap.active, snap.waiting))
        for t in (tb, tc, td, te):
            await t.release()
        snap = await q.snapshot(); out.append((snap.active, snap.waiting))
        return out

    async def run():
        client = aioredis.from_url(os.environ["SRAN_TEST_REDIS_URL"], decode_responses=True)
        prefix = f"sran:turnq:test:{int(time.time()*1000)}"
        try:
            r_out = await scenario(tq.RedisTurnQueueStore(client, prefix=prefix))
        finally:
            keys = await client.keys(prefix + "*")
            if keys:
                await client.delete(*keys)
            await client.aclose()
        m_out = await scenario(MemoryTurnQueueStore())
        assert r_out == m_out, (r_out, m_out)

    _run(run())


# ── the route: a waiting stream opens at once and says where it stands ──────
#
# Starlette's TestClient hands back a response only once its body is
# complete, so a stream that is deliberately held open can never be observed
# through it. These tests run the real route under uvicorn on a loopback
# port and read the SSE bytes as they arrive (httpx streaming).


def _free_port() -> int:
    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        return sk.getsockname()[1]


@contextlib.contextmanager
def _served(app):
    import uvicorn

    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
    server = uvicorn.Server(config)
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        th.join(5)


def _stream_app(monkeypatch, gate: threading.Event):
    """The real chat route with the pipeline stubbed: the first request's
    stream blocks on ``gate``; every request's pipeline yields two frames."""
    from app.api import deps
    from app.api.routes import public_chat
    from app.core.db import get_async_db

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
        mine = calls["n"]

        async def gen():
            yield "data: " + json.dumps({"choices": [{"delta": {"content": f"hello-{mine}"}}]}) + "\n\n"
            if mine == 1:
                while not gate.is_set():
                    await asyncio.sleep(0.01)
            yield "data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    monkeypatch.setattr(public_chat, "_chat_completions_impl", fake_impl)

    async def no_row(**kw):
        return False

    monkeypatch.setattr(public_chat.pre_provider_log, "record_failure", no_row)
    return app


def _data_lines(resp):
    for line in resp.iter_lines():
        if line.startswith("data: "):
            yield line[6:]


BODY = {"model": "m", "messages": [{"role": "user", "content": "hi"}], "stream": True}


def _hold_first_turn(base: str, started: threading.Event, frames: list, headers=None) -> threading.Thread:
    import httpx

    def run():
        with httpx.Client(timeout=30) as c:
            with c.stream("POST", base + "/v1/chat/completions", json=BODY, headers=headers or {}) as r:
                for f in _data_lines(r):
                    frames.append(f)
                    started.set()

    th = threading.Thread(target=run, daemon=True)
    th.start()
    return th


def test_stream_that_must_wait_opens_now_with_queued_frames(monkeypatch):
    import httpx

    tq.reset_for_tests()
    tq.configure(QueueConfig(max_active=1, max_per_identity=1, max_waiting=4, max_wait_s=5, poll_s=0.02, frame_every_s=60),
                 store=MemoryTurnQueueStore())
    try:
        gate = threading.Event()
        app = _stream_app(monkeypatch, gate)
        with _served(app) as base:
            first_frames: list = []
            first_started = threading.Event()
            th = _hold_first_turn(base, first_started, first_frames, {"X-Request-Id": "turn-11111111"})
            assert first_started.wait(5), "first stream never started"
            # the slot is held: the second caller is told it waits, at once
            second_frames: list = []
            with httpx.Client(timeout=30) as c:
                with c.stream("POST", base + "/v1/chat/completions", json=BODY, headers={"X-Request-Id": "turn-22222222"}) as r:
                    assert r.status_code == 200
                    assert r.headers["content-type"].startswith("text/event-stream")
                    assert r.headers.get("x-turn-id") == "turn-22222222"
                    it = _data_lines(r)
                    queued = json.loads(next(it))
                    assert queued["type"] == "queued" and queued["position"] == 1 and queued["ahead"] == 0
                    assert queued["max_active"] == 1 and queued["active"] == 1
                    gate.set()  # the first turn finishes → the slot frees
                    second_frames.extend(it)
            th.join(5)
        admitted = [json.loads(f) for f in second_frames if f.startswith("{") and json.loads(f).get("type") == "queued"]
        assert admitted and admitted[-1]["position"] == 0, second_frames
        assert any("hello-2" in f for f in second_frames) and second_frames[-1] == "[DONE]"
        assert any("hello-1" in f for f in first_frames) and first_frames[-1] == "[DONE]"
        snap = asyncio.run(tq.turn_queue().snapshot())
        assert (snap.active, snap.waiting) == (0, 0), "both slots released"
    finally:
        tq.reset_for_tests()


def test_a_full_line_is_refused_with_retry_after(monkeypatch):
    import httpx

    tq.reset_for_tests()
    tq.configure(QueueConfig(max_active=1, max_per_identity=1, max_waiting=1, max_wait_s=5, poll_s=0.02),
                 store=MemoryTurnQueueStore())
    try:
        gate = threading.Event()
        app = _stream_app(monkeypatch, gate)
        with _served(app) as base:
            started = threading.Event()
            th = _hold_first_turn(base, started, [])
            assert started.wait(5)
            waiting = threading.Event()
            tw = _hold_first_turn(base, waiting, [])  # the one waiter max_waiting allows
            assert waiting.wait(5)  # its first frame is the queued frame
            with httpx.Client(timeout=30) as c:
                r = c.post(base + "/v1/chat/completions", json={**BODY, "stream": False})
            assert r.status_code == 503
            assert r.headers.get("retry-after")
            assert r.json()["error"]["code"] == "queue_full" and r.json()["error"]["retryable"] is True
            gate.set()
            th.join(5)
            tw.join(5)
    finally:
        tq.reset_for_tests()


def test_background_purposes_and_a_disabled_queue_skip_the_line(monkeypatch):
    import httpx

    tq.reset_for_tests()
    tq.configure(QueueConfig(max_active=1, max_per_identity=1, max_waiting=1, max_wait_s=5, poll_s=0.02),
                 store=MemoryTurnQueueStore())
    try:
        gate = threading.Event()
        app = _stream_app(monkeypatch, gate)
        with _served(app) as base:
            started = threading.Event()
            th = _hold_first_turn(base, started, [])
            assert started.wait(5)
            with httpx.Client(timeout=30) as c:
                with c.stream("POST", base + "/v1/chat/completions", json={**BODY, "metadata": {"purpose": "title"}}) as r:
                    frames = list(_data_lines(r))
            assert not any('"queued"' in f for f in frames) and any("hello-2" in f for f in frames)
            gate.set()
            th.join(5)
        # disabled → configure returns None and the route never queues
        assert tq.configure(QueueConfig(enabled=False)) is None and tq.turn_queue() is None
    finally:
        tq.reset_for_tests()
