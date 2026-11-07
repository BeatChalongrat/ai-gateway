"""FIX125 M7 — sync DB work on a fallback / policy path must not block the loop.

The fallback policy's kill switch and overrides (``system_settings``), the
Layer-1 key resolver and key invalidation, and the in-generator streaming
rescue's candidate selection all do SYNC database reads. Called straight
from a coroutine, every one of them froze the whole worker for as long as
the read took (3.7 s measured in production).

Each test replaces the sync piece with one that sleeps and records the
window it ran in, while a ticker task on the same loop records a timestamp
every 10 ms. A blocked loop records no tick inside the window; a call that
runs on a worker thread leaves the ticker running.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.exceptions import ProviderError, ProviderUnavailableError
from app.domain.providers import ProviderResponse, ProviderStreamChunk
from app.services.gateway import fallback_policy
from app.services.gateway.fallback_orchestrator import FallbackOrchestrator
from app.services.provider_dispatcher import ProviderDispatcher
from app.services.providers.openai_adapter import OpenAIAdapter

_SLOW_S = 0.4

# conftest autouse-stubs ``dispatch_with_fallback``; keep the real one
# (captured at import, before any fixture runs) for the Layer-1 tests.
_REAL_DISPATCH_WITH_FALLBACK = ProviderDispatcher.dispatch_with_fallback


class _Ticker:
    def __init__(self):
        self.ticks: list[float] = []
        self._stop = False
        self._task: asyncio.Task | None = None

    async def _run(self):
        while not self._stop:
            self.ticks.append(time.perf_counter())
            await asyncio.sleep(0.01)

    def start(self):
        self._task = asyncio.create_task(self._run())

    async def stop(self):
        self._stop = True
        if self._task is not None:
            await self._task

    def max_gap_inside(self, start: float, end: float) -> float:
        """The longest stretch of [start, end] the loop did not tick in."""
        inside = [t for t in self.ticks if start <= t <= end]
        points = [start, *inside, end]
        return max(b - a for a, b in zip(points, points[1:]))


class _Windows:
    def __init__(self):
        self.spans: list[tuple[float, float]] = []

    def slow(self, fn):
        def _wrapped(*a, **kw):
            t0 = time.perf_counter()
            time.sleep(_SLOW_S)
            try:
                return fn(*a, **kw)
            finally:
                self.spans.append((t0, time.perf_counter()))
        return _wrapped


def _assert_loop_kept_ticking(ticker: _Ticker, windows: _Windows):
    assert windows.spans, "the slow sync call was never reached"
    for start, end in windows.spans:
        gap = ticker.max_gap_inside(start, end)
        assert gap < 0.1, (
            f"event loop blocked for {gap * 1000:.0f} ms while a sync DB call ran"
        )


@pytest.fixture(autouse=True)
def _fresh_policy_cache():
    fallback_policy.clear_cache()
    yield
    fallback_policy.clear_cache()


# ---------------------------------------------------------------------------
# Layer-1 dispatch: the kill-switch read and the fallback key resolver
# ---------------------------------------------------------------------------


@dataclass
class _Route:
    provider_name: str
    model_name: str
    fallbacks: list = field(default_factory=list)


def _dispatcher_with_failing_primary(monkeypatch):
    dispatcher = ProviderDispatcher.__new__(ProviderDispatcher)
    dispatcher.adapters = {"openai": object(), "anthropic": object()}

    async def _dispatch(*args, **kw):
        route = kw["route"] if "route" in kw else args[0]
        if route.provider_name == "openai":
            raise ProviderUnavailableError("upstream 503", details={"provider": "openai", "status": 503})
        return ProviderResponse(
            content="ok", model=route.model_name, finish_reason="stop",
            usage=None, raw={}, reasoning_content=None,
        )

    monkeypatch.setattr(dispatcher, "dispatch", _dispatch)
    monkeypatch.setattr(
        ProviderDispatcher, "dispatch_with_fallback", _REAL_DISPATCH_WITH_FALLBACK
    )
    route = _Route("openai", "gpt-4o-mini", [("anthropic", "claude-sonnet-4")])
    return dispatcher, route


class _Payload:
    metadata: dict = {}


@pytest.mark.asyncio
async def test_layer1_kill_switch_read_does_not_block_the_loop(monkeypatch):
    windows = _Windows()
    monkeypatch.setattr(fallback_policy, "_read_setting", windows.slow(lambda key: None))
    dispatcher, route = _dispatcher_with_failing_primary(monkeypatch)

    ticker = _Ticker()
    ticker.start()
    await asyncio.sleep(0.03)
    resp, provider, _model = await dispatcher.dispatch_with_fallback(
        route=route, payload=_Payload(), messages=[], api_key="k",
        key_resolver=lambda p, m=None: "fallback-key",
    )
    await ticker.stop()

    assert provider == "anthropic" and resp.content == "ok"
    _assert_loop_kept_ticking(ticker, windows)


@pytest.mark.asyncio
async def test_layer1_fallback_key_lookup_does_not_block_the_loop(monkeypatch):
    fallback_policy.is_enabled()  # warm the kill-switch cache: only the key read is slow
    windows = _Windows()
    dispatcher, route = _dispatcher_with_failing_primary(monkeypatch)

    ticker = _Ticker()
    ticker.start()
    await asyncio.sleep(0.03)
    resp, provider, _model = await dispatcher.dispatch_with_fallback(
        route=route, payload=_Payload(), messages=[], api_key="k",
        key_resolver=windows.slow(lambda p, m=None: "fallback-key"),
    )
    await ticker.stop()

    assert provider == "anthropic" and resp.content == "ok"
    _assert_loop_kept_ticking(ticker, windows)


# ---------------------------------------------------------------------------
# Streaming: the per-request policy read and the in-generator rescue lookup
# ---------------------------------------------------------------------------

_STREAM_PAYLOAD = {
    "model": "gpt-4o-mini",
    "messages": [{"role": "user", "content": "loop blocking probe"}],
    "metadata": {},
    "stream": True,
}


@pytest.fixture()
def _no_health_markers():
    from app.services._redis_pool import run_sync
    from app.services.gateway.fallback_orchestrator import clear_provider_health

    def _clear():
        for provider in ("openai", "anthropic"):
            try:
                run_sync(clear_provider_health(provider, "gpt-4o-mini"))
                run_sync(clear_provider_health(provider))
            except Exception:  # noqa: BLE001
                pass

    _clear()
    yield
    _clear()


async def _run_stream_with_ticker() -> tuple[_Ticker, str]:
    from app.main import app

    ticker = _Ticker()
    ticker.start()
    await asyncio.sleep(0.03)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer demo-key"},
            json=_STREAM_PAYLOAD,
        )
    await ticker.stop()
    return ticker, resp.text


@pytest.mark.asyncio
async def test_stream_policy_read_does_not_block_the_loop(monkeypatch, _no_health_markers):
    windows = _Windows()
    monkeypatch.setattr(fallback_policy, "_read_setting", windows.slow(lambda key: None))

    async def _answer(
        self, payload, messages, *, resolved_model=None, api_key=None,
    ) -> AsyncIterator[ProviderStreamChunk]:
        yield ProviderStreamChunk(delta="an answer " * 20, finish_reason="stop", model="gpt-4o-mini")

    monkeypatch.setattr(OpenAIAdapter, "stream_chat", _answer)

    ticker, body = await _run_stream_with_ticker()
    assert "an answer" in body
    _assert_loop_kept_ticking(ticker, windows)


@pytest.mark.asyncio
async def test_stream_rescue_lookup_does_not_block_the_loop(monkeypatch, _no_health_markers):
    fallback_policy.is_enabled()
    fallback_policy.effective_policy(None)
    windows = _Windows()
    monkeypatch.setattr(
        FallbackOrchestrator,
        "select_streaming_candidate",
        windows.slow(lambda self, **kw: None),
    )

    async def _fail(
        self, payload, messages, *, resolved_model=None, api_key=None,
    ) -> AsyncIterator[ProviderStreamChunk]:
        raise ProviderError("upstream 503", details={"provider": "openai", "status": 503})
        yield  # pragma: no cover

    monkeypatch.setattr(OpenAIAdapter, "stream_chat", _fail)

    ticker, body = await _run_stream_with_ticker()
    assert "all_models_failed" in body
    _assert_loop_kept_ticking(ticker, windows)


# ---------------------------------------------------------------------------
# The async entry points return what the sync ones return
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_policy_reads_match_the_sync_ones(monkeypatch):
    monkeypatch.setattr(fallback_policy, "_read_setting", lambda key: None)
    assert await fallback_policy.is_enabled_async() == fallback_policy.is_enabled()
    # No override stored → the shipped policy object itself (identity).
    assert await fallback_policy.effective_policy_async("t1") is fallback_policy.base_policy()
    fallback_policy.clear_cache()
    assert await fallback_policy.effective_policy_async("t1") is fallback_policy.base_policy()
