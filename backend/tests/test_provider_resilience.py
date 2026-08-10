"""Batch A — provider resilience: per-provider concurrency bulkhead,
pool-size env/overrides, retry jitter + 504 + Retry-After.

The semaphore registry is process-global and loop-bound, so every test
resets it and patches the caps map on the settings instance.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from app.core.config import settings
from app.core.exceptions import ProviderAtCapacityError
from app.services.providers import concurrency
from app.services.providers.concurrency import guarded_stream, provider_slot
from app.services.providers.error_handler import (
    _MAX_RETRY_SLEEP_SECONDS,
    _RETRYABLE_STATUS,
    _backoff_delay,
    call_with_retry,
)


@pytest.fixture(autouse=True)
def _reset_semaphores():
    concurrency.reset_provider_semaphores()
    yield
    concurrency.reset_provider_semaphores()


@pytest.fixture()
def capped(monkeypatch):
    """Cap 'boxprov' at 2 with a very short acquire timeout."""
    monkeypatch.setattr(settings, "provider_concurrency_caps", '{"boxprov": 2}')
    monkeypatch.setattr(
        settings, "provider_concurrency_acquire_timeout_seconds", 0.1
    )


# ---------------------------------------------------------------------------
# provider_slot: cap semantics
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_uncapped_provider_is_a_noop():
    # No caps configured at all → no semaphore is even created.
    async with provider_slot("openai"):
        pass
    assert concurrency._semaphores == {}


@pytest.mark.asyncio
async def test_excess_concurrency_sheds_with_503_metadata(capped):
    release = asyncio.Event()
    entered = 0

    async def hold():
        nonlocal entered
        async with provider_slot("boxprov"):
            entered += 1
            await release.wait()

    holders = [asyncio.create_task(hold()) for _ in range(2)]
    await asyncio.sleep(0.02)  # let both acquire
    assert entered == 2

    # Third concurrent request: slot never frees within 0.1s → shed.
    with pytest.raises(ProviderAtCapacityError) as excinfo:
        async with provider_slot("boxprov"):
            pytest.fail("third request must not get a slot")

    exc = excinfo.value
    assert exc.code == "provider_at_capacity"
    assert exc.details["provider"] == "boxprov"
    assert exc.details["cap"] == 2
    assert exc.headers["Retry-After"] == "1"

    release.set()
    await asyncio.gather(*holders)

    # Slots were released — a new request acquires instantly.
    async with provider_slot("boxprov"):
        pass


@pytest.mark.asyncio
async def test_slot_released_on_exception(capped):
    with pytest.raises(RuntimeError):
        async with provider_slot("boxprov"):
            raise RuntimeError("upstream blew up")
    # Both slots free again.
    async with provider_slot("boxprov"):
        async with provider_slot("boxprov"):
            pass


@pytest.mark.asyncio
async def test_at_capacity_error_is_fallback_eligible(capped):
    from app.services.gateway.fallback_orchestrator import is_fallback_eligible

    assert is_fallback_eligible(ProviderAtCapacityError()) is True


# ---------------------------------------------------------------------------
# guarded_stream: release across the generator lifecycle
# ---------------------------------------------------------------------------

async def _three_chunks():
    for i in range(3):
        yield i


@pytest.mark.asyncio
async def test_stream_slot_held_during_and_released_after(capped):
    chunks = []
    gen = guarded_stream("boxprov", _three_chunks)
    async for c in gen:
        chunks.append(c)
        # While mid-stream, one of the two slots is held.
        assert concurrency._semaphores["boxprov"]._value == 1
    assert chunks == [0, 1, 2]
    assert concurrency._semaphores["boxprov"]._value == 2


@pytest.mark.asyncio
async def test_stream_slot_released_on_upstream_error(capped):
    async def exploding():
        yield 0
        raise httpx.ReadError("upstream died mid-stream")

    with pytest.raises(httpx.ReadError):
        async for _ in guarded_stream("boxprov", exploding):
            pass
    assert concurrency._semaphores["boxprov"]._value == 2


@pytest.mark.asyncio
async def test_stream_slot_released_on_client_disconnect(capped):
    # Consumer abandons the stream (GeneratorExit path — what a client
    # disconnect does to the SSE generator).
    gen = guarded_stream("boxprov", _three_chunks)
    assert await gen.__anext__() == 0
    assert concurrency._semaphores["boxprov"]._value == 1
    await gen.aclose()
    assert concurrency._semaphores["boxprov"]._value == 2


@pytest.mark.asyncio
async def test_stream_capacity_error_raised_before_upstream_opened(capped):
    opened = False

    async def tracking():
        nonlocal opened
        opened = True
        yield 0

    release = asyncio.Event()

    async def hold():
        async with provider_slot("boxprov"):
            await release.wait()

    holders = [asyncio.create_task(hold()) for _ in range(2)]
    await asyncio.sleep(0.02)

    with pytest.raises(ProviderAtCapacityError):
        async for _ in guarded_stream("boxprov", tracking):
            pass
    assert opened is False  # slot denied → upstream never dialed

    release.set()
    await asyncio.gather(*holders)


# ---------------------------------------------------------------------------
# Dispatcher integration: N+1 concurrent through the real dispatch()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_sheds_excess_and_uncapped_unaffected(capped, monkeypatch):
    # conftest's autouse fixture stubs ProviderDispatcher.dispatch for
    # integration tests; this test needs the REAL dispatch (the bulkhead
    # wrap lives inside it). Execute a pristine copy of the module
    # WITHOUT touching sys.modules — importlib.reload would swap the
    # module for every later test and detach them from the conftest
    # stub (observed as order-dependent contract-test failures).
    import importlib.util

    spec = importlib.util.find_spec("app.services.provider_dispatcher")
    fresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fresh)
    ProviderDispatcher = fresh.ProviderDispatcher

    release = asyncio.Event()

    class SlowAdapter:
        supports_streaming = True

        async def chat(self, *, payload, messages, resolved_model, api_key):
            await release.wait()
            return "ok"

    dispatcher = ProviderDispatcher()
    dispatcher.adapters["boxprov"] = SlowAdapter()

    class Route:
        provider_name = "boxprov"
        model_name = "m"

    async def one_call():
        try:
            return await dispatcher.dispatch(
                route=Route(), payload=None, messages=[], api_key="k"
            )
        except ProviderAtCapacityError:
            return "shed"

    tasks = [asyncio.create_task(one_call()) for _ in range(3)]
    await asyncio.sleep(0.3)  # > acquire timeout: the loser has shed
    release.set()
    results = await asyncio.gather(*tasks)

    assert sorted(results) == ["ok", "ok", "shed"]

    # Uncapped provider takes the fast path (no semaphore entry).
    class FastAdapter:
        async def chat(self, **kwargs):
            return "fast"

    dispatcher.adapters["openai"] = FastAdapter()

    class OpenAIRoute:
        provider_name = "openai"
        model_name = "gpt-4o"

    assert await dispatcher.dispatch(
        route=OpenAIRoute(), payload=None, messages=[], api_key="k"
    ) == "fast"
    assert "openai" not in concurrency._semaphores


# ---------------------------------------------------------------------------
# httpx pool: env defaults + per-provider override
# ---------------------------------------------------------------------------

def test_pool_limits_default_match_previous_constants():
    from app.services.providers.http_client import _pool_limits_for

    limits = _pool_limits_for(None)
    assert limits.max_connections == 50
    assert limits.max_keepalive_connections == 20
    assert limits.keepalive_expiry == 30.0


def test_pool_limits_per_provider_override(monkeypatch):
    from app.services.providers.http_client import _pool_limits_for

    monkeypatch.setattr(settings, "provider_pool_overrides", '{"boxprov": 8}')
    limits = _pool_limits_for("boxprov")
    assert limits.max_connections == 8
    # Keepalive clamps to the connection cap.
    assert limits.max_keepalive_connections == 8
    # Other providers keep the global default.
    assert _pool_limits_for("openai").max_connections == 50


def test_pool_env_defaults_change_when_settings_change(monkeypatch):
    from app.services.providers.http_client import _pool_limits_for

    monkeypatch.setattr(settings, "provider_pool_max_connections", 12)
    monkeypatch.setattr(settings, "provider_pool_max_keepalive_connections", 6)
    limits = _pool_limits_for(None)
    assert limits.max_connections == 12
    assert limits.max_keepalive_connections == 6


def test_bad_override_json_fails_open(monkeypatch):
    monkeypatch.setattr(settings, "provider_pool_overrides", "{not json")
    with pytest.warns(RuntimeWarning):
        assert settings.provider_pool_overrides_map == {}
    monkeypatch.setattr(settings, "provider_concurrency_caps", "[1,2]")
    assert settings.provider_concurrency_caps_map == {}


# ---------------------------------------------------------------------------
# Retry: 504, jitter, Retry-After
# ---------------------------------------------------------------------------

def test_504_is_retryable_now():
    assert 504 in _RETRYABLE_STATUS


def test_backoff_has_jitter_within_equal_jitter_band():
    # Equal jitter: delay ∈ [base·2^n / 2, base·2^n]. Sample repeatedly —
    # values must stay in-band AND actually vary.
    samples = {_backoff_delay(0.5, 1) for _ in range(200)}
    assert all(0.5 <= s <= 1.0 for s in samples)
    assert len(samples) > 10, "no jitter — backoff is deterministic"


def test_backoff_honors_retry_after_as_floor_with_ceiling():
    # Retry-After larger than the computed delay wins…
    assert _backoff_delay(0.5, 0, retry_after=3.0) == 3.0
    # …but never past the constant ceiling.
    assert _backoff_delay(0.5, 0, retry_after=120.0) == _MAX_RETRY_SLEEP_SECONDS


@pytest.mark.asyncio
@respx.mock
async def test_call_with_retry_retries_504_then_succeeds(monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(d):
        sleeps.append(d)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    route = respx.post("https://api.box.test/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(504, text="upstream timeout"),
            httpx.Response(200, json={"ok": True}),
        ]
    )

    async with httpx.AsyncClient() as client:
        resp = await call_with_retry(
            lambda: client.post("https://api.box.test/v1/chat/completions"),
            provider_name="boxprov",
            max_retries=2,
        )
    assert resp.status_code == 200
    assert route.call_count == 2
    assert len(sleeps) == 1 and 0.25 <= sleeps[0] <= 0.5


@pytest.mark.asyncio
@respx.mock
async def test_call_with_retry_honors_retry_after_header(monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(d):
        sleeps.append(d)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    respx.post("https://api.box.test/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(429, text="full", headers={"Retry-After": "4"}),
            httpx.Response(200, json={"ok": True}),
        ]
    )

    async with httpx.AsyncClient() as client:
        resp = await call_with_retry(
            lambda: client.post("https://api.box.test/v1/chat/completions"),
            provider_name="boxprov",
            max_retries=2,
        )
    assert resp.status_code == 200
    assert sleeps == [4.0]  # header floor beat the sub-second backoff


# ---------------------------------------------------------------------------
# Integration fix: at-capacity must survive the no-chain fallback path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_chain_fallback_preserves_at_capacity_503():
    # Found live: a shed request on a chain-less model was wrapped by the
    # generic "No fallback chain" 502, losing 503 + Retry-After. The
    # orchestrator passes the primary error in; the no-chain branch must
    # re-raise it unchanged.
    from app.services.gateway.fallback_orchestrator import FallbackOrchestrator
    from app.core.exceptions import ProviderError

    class _Ctx:
        tenant_id = "t"
        project_id = "p"
        request_id = "r"

    runner = FallbackOrchestrator(dispatcher=None, key_service=None)
    original = ProviderAtCapacityError(headers={"Retry-After": "1"})

    with pytest.raises(ProviderAtCapacityError) as excinfo:
        await runner.run(
            original_provider="boxprov",
            original_model="model-with-no-chain",
            request=None,
            messages=[],
            session=None,
            ctx=_Ctx(),
            original_error=original,
        )
    assert excinfo.value is original  # same object — headers intact

    # A NON-capacity error still gets the explanatory no-chain wrapper.
    with pytest.raises(ProviderError) as generic:
        await runner.run(
            original_provider="boxprov",
            original_model="model-with-no-chain",
            request=None,
            messages=[],
            session=None,
            ctx=_Ctx(),
            original_error=ProviderError("upstream 500"),
        )
    assert "No fallback chain" in generic.value.message
