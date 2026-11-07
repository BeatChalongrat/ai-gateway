"""FIX134 E1 — a stopped stream closes its body at once, not at the next GC.

Root cause (live, 2026-09-25): when the client goes away, Starlette's
StreamingResponse cancels its ``stream_response`` task. If the task is
parked in ``await send(...)`` between two frames (the usual place while the
proxy hop drains a burst), the ``async for`` over the body iterator is left
by the CancelledError and the async generator is NOT closed: it stays
suspended at its ``yield`` until the garbage collector finds it. Until then
nothing runs — no ``stream_client_disconnected``, no ledger row, no
request_log, and the provider's HTTP stream stays open. Measured: 3.6 s,
10.5 s, 36 s, 68 s and one > 2 min after the user's Stop on
deepseek-v4-flash; a Stop that lands while the task waits for the next
chunk was closed in 2 ms (bounded_sse's ``finally`` closes the inner
generator on that path).

``ClosingStreamingResponse`` closes the body iterator in a ``finally`` of
``stream_response``, shielded from the cancellation that is tearing the task
down and bounded in time, so every exit path finalises at once.
"""

from __future__ import annotations

import asyncio
import gc
import inspect

import pytest
from starlette.responses import StreamingResponse

from app.core.bounded_stream import ClosingStreamingResponse


async def _stop_while_parked_in_send(resp_cls):
    """Drive ``resp_cls`` like uvicorn: the client leaves after 2 frames and the
    3rd ``send`` is parked (a proxy hop that is not reading any more)."""
    state = {"closed": False, "cleanup_awaited": False, "yielded": 0}

    async def body():
        try:
            for i in range(10_000):
                state["yielded"] += 1
                yield f"data: {i}\n\n"
        finally:
            # cleanup that awaits (settle, release a slot, close the provider
            # stream): must complete although the task is being cancelled
            await asyncio.sleep(0.01)
            state["cleanup_awaited"] = True
            state["closed"] = True

    resp = resp_cls(body(), media_type="text/event-stream")
    sent = 0
    left = asyncio.Event()
    parked = asyncio.Event()  # never set

    async def send(message):
        nonlocal sent
        if message["type"] == "http.response.body":
            sent += 1
            if sent == 2:
                left.set()
            if sent >= 3:
                await parked.wait()

    async def receive():
        await left.wait()
        return {"type": "http.disconnect"}

    await asyncio.wait_for(resp({"type": "http", "method": "GET"}, receive, send), timeout=5)
    await asyncio.sleep(0.2)  # anything scheduled by the teardown gets its turn
    # Snapshot INSIDE the loop: asyncio.run() closes every pending async
    # generator at shutdown, which would hide the leak.
    snapshot = dict(state)
    await asyncio.sleep(0)
    _keep = resp  # the response (and so the generator) stays referenced until here
    return snapshot, _keep


@pytest.fixture()
def no_gc():
    was = gc.isenabled()
    gc.disable()
    yield
    if was:
        gc.enable()


def test_starlette_alone_leaves_the_body_suspended(no_gc):
    """The mechanism, pinned: plain StreamingResponse does not close the body."""
    state, _resp = asyncio.run(_stop_while_parked_in_send(StreamingResponse))
    assert state["closed"] is False, "Starlette changed: plain StreamingResponse now closes the body"


def test_closing_response_closes_the_body_at_once(no_gc):
    state, _resp = asyncio.run(_stop_while_parked_in_send(ClosingStreamingResponse))
    assert state["closed"] is True, "the body generator was left suspended after the client left"
    assert state["cleanup_awaited"] is True, "the body's awaiting cleanup was cut short by the cancellation"
    assert state["yielded"] <= 4, f"the body kept producing after the client left ({state['yielded']} frames)"


def test_closing_response_is_a_noop_on_normal_completion():
    async def body():
        yield "data: 1\n\n"
        yield "data: [DONE]\n\n"

    out = []

    async def send(message):
        out.append(message)

    async def receive():
        await asyncio.sleep(3600)

    asyncio.run(ClosingStreamingResponse(body(), media_type="text/event-stream")({"type": "http", "method": "GET"}, receive, send))
    assert [m.get("body") for m in out if m["type"] == "http.response.body"] == [b"data: 1\n\n", b"data: [DONE]\n\n", b""]


def test_every_chat_stream_uses_the_closing_response():
    from app.api.routes import public_chat

    src = inspect.getsource(public_chat)
    assert "return StreamingResponse(" not in src, "a chat stream is built with the plain StreamingResponse"
    assert src.count("ClosingStreamingResponse(") >= 3
