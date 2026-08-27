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
