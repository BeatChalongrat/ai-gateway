"""FIX49 Part C — health markers are per (provider, model).

The old marker was per provider: one dead model (a 404 on a pulled id)
dabbed the whole provider for 60 s, and with 11 of 14 usable models on
one aggregator that meant one bad model = the whole system down for a
minute (measured live in the 2026-09-03 audit as
``error_code: health_marker_skip`` on healthy models).

Pins the new scoping contract:
  * a model-scoped mark leaves sibling models healthy;
  * only infrastructure-class codes (timeout / unreachable) mark the
    provider wide;
  * a success clears both the pair and the provider key;
  * the 402 mapping: with Retry-After → the retryable rate-limit class,
    without → a fallback-eligible provider error (out of credits).
"""

import asyncio

import httpx
import pytest

from app.core.exceptions import ProviderError, RateLimitExceededError
from app.services.gateway import fallback_orchestrator as fo
from app.services.providers.error_handler import map_provider_error


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    async def set(self, key, value, ex=None):
        self.store[key] = value

    async def delete(self, key):
        self.store.pop(key, None)

    async def exists(self, key):
        return 1 if key in self.store else 0

    def pipeline(self, transaction=True):
        return _FakePipeline(self)


class _FakePipeline:
    def __init__(self, redis: "_FakeRedis"):
        self._redis = redis
        self._keys: list[str] = []

    def exists(self, key):
        self._keys.append(key)
        return self

    async def execute(self):
        return [1 if k in self._redis.store else 0 for k in self._keys]


@pytest.fixture
def fake_redis(monkeypatch):
    r = _FakeRedis()
    monkeypatch.setattr(
        "app.services._redis_pool.get_async_redis", lambda: r
    )
    return r


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_model_scoped_mark_leaves_siblings_healthy(fake_redis):
    _run(fo.mark_provider_unhealthy(
        "openrouter", "anthropic/claude-3.5-haiku",
        error_code="provider_error",
    ))
    assert fake_redis.store == {
        "provider:health:openrouter:anthropic/claude-3.5-haiku": "unhealthy"
    }
    assert _run(fo.is_provider_unhealthy("openrouter", "anthropic/claude-3.5-haiku"))
    # The sibling model — and the provider itself — stay healthy.
    assert not _run(fo.is_provider_unhealthy("openrouter", "google/gemini-2.5-flash"))
    assert not _run(fo.is_provider_unhealthy("openrouter"))


def test_infrastructure_code_marks_the_provider_wide(fake_redis):
    _run(fo.mark_provider_unhealthy(
        "openrouter", "google/gemini-2.5-pro",
        error_code="provider_timeout",
    ))
    # A provider-wide infra failure rules out every model on it.
    assert _run(fo.is_provider_unhealthy("openrouter", "google/gemini-2.5-flash"))
    assert _run(fo.is_provider_unhealthy("openrouter"))


def test_success_clears_pair_and_provider(fake_redis):
    _run(fo.mark_provider_unhealthy(
        "openrouter", "google/gemini-2.5-pro", error_code="provider_timeout"
    ))
    _run(fo.clear_provider_health("openrouter", "google/gemini-2.5-pro"))
    assert fake_redis.store == {}
    assert not _run(fo.is_provider_unhealthy("openrouter", "google/gemini-2.5-pro"))


def test_legacy_provider_only_call_still_works(fake_redis):
    # Callers that pass no model (the auto-router pool filter) keep the
    # provider-wide semantics.
    _run(fo.mark_provider_unhealthy("openai"))
    assert _run(fo.is_provider_unhealthy("openai"))
    assert _run(fo.is_provider_unhealthy("openai", "gpt-4o"))  # wide covers all


# ── FIX50 P1-A: the auto pool filter sees pair markers (OD #58) ────────

def test_unhealthy_pairs_returns_only_marked_pairs(fake_redis):
    _run(fo.mark_provider_unhealthy(
        "openrouter", "deepseek/deepseek-chat", error_code="provider_error"
    ))
    marked = _run(fo.unhealthy_pairs([
        ("openrouter", "deepseek/deepseek-chat"),
        ("openrouter", "google/gemini-2.5-flash"),
        ("anthropic", "claude-sonnet-4"),
    ]))
    assert marked == {("openrouter", "deepseek/deepseek-chat")}


def test_unhealthy_pairs_is_one_pipeline_round_trip_and_fails_open(fake_redis, monkeypatch):
    # Empty input never touches Redis; a Redis failure returns no marks
    # (fail-open, same contract as is_provider_unhealthy).
    assert _run(fo.unhealthy_pairs([])) == set()

    def _boom():
        raise ConnectionError("redis down")

    monkeypatch.setattr("app.services._redis_pool.get_async_redis", _boom)
    assert _run(fo.unhealthy_pairs([("openrouter", "deepseek/deepseek-chat")])) == set()


def test_auto_router_pool_filter_checks_pair_markers():
    # The wiring pin: _auto_route prunes candidates through
    # unhealthy_pairs, not only through the provider-wide lookup.
    import inspect

    from app.services.orchestrator import GatewayOrchestrator

    src = inspect.getsource(GatewayOrchestrator._auto_route)
    assert "unhealthy_pairs" in src


# ── 402 mapping (Part C.4) ─────────────────────────────────────────────

def _http_402(headers: dict) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    resp = httpx.Response(
        402, headers=headers, request=req,
        json={"error": {"message": "in_flight_budget_exhausted"}},
    )
    return httpx.HTTPStatusError("402", request=req, response=resp)


def test_402_with_retry_after_is_retryable_rate_limit():
    exc = map_provider_error(_http_402({"Retry-After": "120"}), "openrouter")
    assert isinstance(exc, RateLimitExceededError)
    assert exc.details["retry_after"] == "120"
    # The catalogue the client reads says this code is retryable.
    from app.core import error_catalog

    assert error_catalog.is_retryable(exc.code) is True


def test_402_without_retry_after_is_fallback_eligible_but_not_a_retry_offer():
    exc = map_provider_error(_http_402({}), "openrouter")
    assert isinstance(exc, ProviderError)
    assert fo.is_fallback_eligible(exc)
