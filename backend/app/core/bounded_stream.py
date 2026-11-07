"""FIX26 §2.2 — a hard timeout around a streaming (SSE) response body.

The provider client already has per-read and pool timeouts; what was
missing is a bound on the WHOLE request path that a hung stream sits in
(DLP, DB, cache, provider, restore). ``bounded_sse`` wraps any async
frame generator with

* an IDLE timeout — no frame for ``idle_timeout_s`` → the stream ends
  with an SSE error frame + ``[DONE]`` for THAT client only; and
* a HARD timeout — total wall time ``hard_timeout_s`` → same.

On either, the inner generator is closed (``aclose()``) so its
``finally`` blocks (billing settle, request_log persist) still run. The
error frame carries the request id so the user can quote it. Pure asyncio;
unit-tested with fake generators.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator


def error_frame(kind: str, message: str, request_id: str | None) -> str:
    body = {
        "error": {"type": kind, "message": message, "request_id": request_id or ""},
    }
    return f"data: {json.dumps(body, ensure_ascii=False)}\n\n"


async def bounded_sse(
    frames: AsyncIterator[str],
    *,
    idle_timeout_s: float,
    hard_timeout_s: float,
    request_id: str | None = None,
    on_timeout=None,
) -> AsyncIterator[str]:
    """Yield frames from ``frames`` until it ends or a bound trips."""
    started = time.monotonic()
    agen = frames.__aiter__()
    try:
        while True:
            remaining_hard = hard_timeout_s - (time.monotonic() - started)
            if remaining_hard <= 0:
                yield error_frame("stream_timeout", f"stream exceeded {hard_timeout_s:.0f}s and was closed", request_id)
                yield "data: [DONE]\n\n"
                if on_timeout:
                    on_timeout("hard")
                return
            wait = min(idle_timeout_s, remaining_hard)
            try:
                frame = await asyncio.wait_for(agen.__anext__(), timeout=wait)
            except StopAsyncIteration:
                return
            except asyncio.TimeoutError:
                kind = "hard" if remaining_hard <= idle_timeout_s else "idle"
                msg = (
                    f"stream exceeded {hard_timeout_s:.0f}s and was closed"
                    if kind == "hard"
                    else f"no data from the upstream for {idle_timeout_s:.0f}s; the request was closed"
                )
                yield error_frame("stream_timeout", msg, request_id)
                yield "data: [DONE]\n\n"
                if on_timeout:
                    on_timeout(kind)
                return
            yield frame
    finally:
        # Close the inner generator so its finally/billing hooks run. The
        # wait_for above cancelled the pending __anext__; aclose finishes
        # the job. Never let a close error mask the client's frames.
        try:
            await agen.aclose()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# FIX134 E1 — a stopped stream closes its body at once, not at the next GC.
#
# When the client goes away, Starlette's StreamingResponse cancels its
# ``stream_response`` task. If that task is parked in ``await send(...)``
# between two frames — the usual place while a proxy hop drains a burst —
# the ``async for`` over the body iterator is left by the CancelledError and
# the async generator is NOT closed: it stays suspended at its ``yield``
# until the garbage collector finds it (the chat generators sit in reference
# cycles, so that is a full collection: seconds to minutes). Until then
# nothing runs — no ``stream_client_disconnected``, no ledger row, no
# request_log, the turn-queue slot stays held and the provider's HTTP stream
# stays open. Live on deepseek-v4-flash: 3.6 s, 10.5 s, 36 s, 68 s and one
# over two minutes after the user's Stop, while a Stop that landed during
# the wait for the next chunk closed in 2 ms (bounded_sse's ``finally``
# above closes the inner generator on that path).
#
# The fix closes the body iterator in a ``finally`` of ``stream_response``,
# shielded from the cancellation that is tearing the task down (anyio
# re-delivers it at every await) and bounded in time, so every exit path —
# normal end, client gone, error — finalises at once. A body that already
# finished makes ``aclose()`` a no-op; a body that is still running in
# another task (bounded_sse's wait_for) raises "already running", which is
# fine: that task is finishing it.
# ---------------------------------------------------------------------------
import anyio  # noqa: E402
import structlog  # noqa: E402
from starlette.responses import StreamingResponse as _StarletteStreamingResponse  # noqa: E402
from starlette.types import Send  # noqa: E402

_close_log = structlog.get_logger("bounded_stream")


class ClosingStreamingResponse(_StarletteStreamingResponse):
    """StreamingResponse that always closes its body iterator when it stops."""

    close_timeout_s: float = 10.0

    async def stream_response(self, send: Send) -> None:
        try:
            await super().stream_response(send)
        finally:
            await self._close_body()

    async def _close_body(self) -> None:
        aclose = getattr(self.body_iterator, "aclose", None)
        if aclose is None:
            return
        with anyio.move_on_after(self.close_timeout_s, shield=True) as scope:
            try:
                await aclose()
            except RuntimeError:
                # "aclose(): asynchronous generator is already running" — the
                # generator is mid-step in another task that is being
                # cancelled and will finish it itself.
                pass
            except Exception as exc:  # noqa: BLE001 — cleanup never masks the response's own exit
                _close_log.warning("stream_body_close_failed", error=type(exc).__name__, detail=str(exc)[:200])
        if scope.cancelled_caught:
            _close_log.warning("stream_body_close_timeout", timeout_s=self.close_timeout_s)
