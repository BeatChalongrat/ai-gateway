"""Unit tests for the Phase-2 FallbackOrchestrator.

Scenarios covered:

* primary success (no fallback) — covered indirectly by the integration
  test_suite via the autouse provider stub
* fallback-1 success
* fallback-2 success (first fallback fails, second works)
* all fallbacks fail (last error re-raised)
* no API key on a candidate → skipped without consuming an attempt slot
* non-eligible error → no fallback triggered (is_fallback_eligible check)
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.core.exceptions import (
    GatewayError,
    ProviderError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ValidationError,
)
from app.domain.entities import RequestContext
from app.domain.providers import ProviderResponse
from app.services.gateway.fallback_orchestrator import (
    FALLBACK_ELIGIBLE_ERRORS,
    FallbackOrchestrator,
    is_fallback_eligible,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ctx() -> RequestContext:
    return RequestContext(
        tenant_id="tenant-x",
        project_id="proj-x",
        project_name="Test",
        request_id="req_unit",
    )


def _make_payload() -> object:
    """Minimal payload stub — the orchestrator only touches ``model``."""
    p = MagicMock()
    p.model = "gpt-4o"
    return p


def _ok_response(model: str) -> ProviderResponse:
    return ProviderResponse(
        content="ok",
        model=model,
        finish_reason="stop",
    )


class _FakeKeyService:
    """Returns a configured key for every provider in ``keys_for``."""

    def __init__(self, keys_for: set[str]) -> None:
        self.keys_for = keys_for

    def get_api_key(self, session, provider: str) -> str | None:
        return f"sk-{provider}" if provider in self.keys_for else None

    async def get_api_key_async(self, session, provider: str) -> str | None:
        # The FallbackOrchestrator.run() hot path was refactored to the
        # async key lookup; mirror the sync stub so the fake satisfies it.
        return self.get_api_key(session, provider)


# ---------------------------------------------------------------------------
# is_fallback_eligible
# ---------------------------------------------------------------------------

def test_eligible_includes_canonical_codes():
    assert "provider_timeout" in FALLBACK_ELIGIBLE_ERRORS
    assert "provider_unavailable" in FALLBACK_ELIGIBLE_ERRORS
    assert "rate_limit_exceeded" in FALLBACK_ELIGIBLE_ERRORS


def test_eligible_for_provider_timeout():
    exc = ProviderTimeoutError("upstream timed out")
    assert is_fallback_eligible(exc) is True


def test_eligible_for_provider_unavailable():
    exc = ProviderUnavailableError("upstream down")
    assert is_fallback_eligible(exc) is True


def test_not_eligible_for_validation_error():
    exc = ValidationError("bad input")
    assert is_fallback_eligible(exc) is False


def test_not_eligible_for_dlp_violation():
    from app.core.exceptions import DLPViolationError
    exc = DLPViolationError("blocked")
    assert is_fallback_eligible(exc) is False


# ---------------------------------------------------------------------------
# FallbackOrchestrator.run — happy paths
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fallback_1_succeeds_first_try():
    """The first candidate (anthropic for a gpt-4o primary) succeeds."""

    class _Dispatcher:
        def __init__(self):
            self.calls = []

        async def dispatch(self, *, route, payload, messages, api_key):
            self.calls.append((route.provider_name, route.model_name))
            return _ok_response(route.model_name)

    dispatcher = _Dispatcher()
    runner = FallbackOrchestrator(
        dispatcher=dispatcher,
        key_service=_FakeKeyService(
            {"anthropic", "google", "openrouter"}
        ),
    )
    result = await runner.run(
        original_provider="openai",
        original_model="gpt-4o",
        request=_make_payload(),
        messages=[],
        session=object(),  # truthy stub
        ctx=_make_ctx(),
    )

    assert result.provider_used == "anthropic"
    assert result.model_used == "claude-opus-4"
    assert result.attempt_number == 2  # primary=1, fallback-1=2
    assert result.attempts == []
    assert dispatcher.calls == [("anthropic", "claude-opus-4")]


@pytest.mark.asyncio
async def test_fallback_2_succeeds_after_first_fails():
    """First fallback (anthropic) raises, second (google) succeeds."""

    class _Dispatcher:
        def __init__(self):
            self.calls = []

        async def dispatch(self, *, route, payload, messages, api_key):
            self.calls.append((route.provider_name, route.model_name))
            if route.provider_name == "anthropic":
                raise ProviderTimeoutError(
                    "anthropic timed out",
                    details={"provider": "anthropic"},
                )
            return _ok_response(route.model_name)

    dispatcher = _Dispatcher()
    runner = FallbackOrchestrator(
        dispatcher=dispatcher,
        key_service=_FakeKeyService(
            {"anthropic", "google", "openrouter"}
        ),
    )
    result = await runner.run(
        original_provider="openai",
        original_model="gpt-4o",
        request=_make_payload(),
        messages=[],
        session=object(),
        ctx=_make_ctx(),
    )

    assert result.provider_used == "google"
    assert result.model_used == "gemini-1.5-pro"
    # attempts list carries the failed anthropic try
    assert len(result.attempts) == 1
    assert result.attempts[0].provider == "anthropic"
    assert result.attempts[0].error_code == "provider_timeout"
    # primary=1, fallback-1 (failed) counted in attempts, fallback-2 success → 3
    assert result.attempt_number == 3
    assert dispatcher.calls == [
        ("anthropic", "claude-opus-4"),
        ("google", "gemini-1.5-pro"),
    ]


# ---------------------------------------------------------------------------
# Sad paths
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_all_fallbacks_fail_raises_last_error():
    """When max_candidates attempts all fail, the last error escapes."""

    class _Dispatcher:
        def __init__(self):
            self.calls = 0

        async def dispatch(self, *, route, payload, messages, api_key):
            self.calls += 1
            # Different error per attempt so we can detect which one
            # comes out — last one should win.
            if self.calls == 1:
                raise ProviderTimeoutError("a-timeout")
            raise ProviderUnavailableError("b-down")

    dispatcher = _Dispatcher()
    runner = FallbackOrchestrator(
        dispatcher=dispatcher,
        key_service=_FakeKeyService(
            {"anthropic", "google", "openrouter"}
        ),
    )
    with pytest.raises(ProviderUnavailableError) as excinfo:
        await runner.run(
            original_provider="openai",
            original_model="gpt-4o",
            request=_make_payload(),
            messages=[],
            session=object(),
            ctx=_make_ctx(),
        )
    assert "b-down" in str(excinfo.value)
    assert dispatcher.calls == 2  # max_candidates default


@pytest.mark.asyncio
async def test_no_api_key_skips_candidate_without_consuming_slot():
    """A candidate without a configured API key is recorded and skipped,
    then the next candidate still gets a full attempt slot."""

    class _Dispatcher:
        def __init__(self):
            self.calls = []

        async def dispatch(self, *, route, payload, messages, api_key):
            self.calls.append(route.provider_name)
            return _ok_response(route.model_name)

    dispatcher = _Dispatcher()
    # Only google + openrouter have keys; anthropic (first in chain) is
    # missing one, so the runner records a no_api_key attempt then
    # dispatches to google as if it were the first attempt.
    runner = FallbackOrchestrator(
        dispatcher=dispatcher,
        key_service=_FakeKeyService({"google", "openrouter"}),
    )
    result = await runner.run(
        original_provider="openai",
        original_model="gpt-4o",
        request=_make_payload(),
        messages=[],
        session=object(),
        ctx=_make_ctx(),
    )

    assert dispatcher.calls == ["google"]
    assert result.provider_used == "google"
    # The skipped anthropic attempt is recorded in attempts but
    # didn't consume a real candidate slot.
    assert any(
        a.provider == "anthropic" and a.error_code == "no_api_key"
        for a in result.attempts
    )


@pytest.mark.asyncio
async def test_no_chain_for_unknown_model_raises_provider_error():
    """An unknown primary model can't yield a fallback chain."""

    class _Dispatcher:
        async def dispatch(self, *, route, payload, messages, api_key):
            return _ok_response("never")

    runner = FallbackOrchestrator(
        dispatcher=_Dispatcher(),
        key_service=_FakeKeyService({"anthropic"}),
    )
    with pytest.raises(ProviderError):
        await runner.run(
            original_provider="openai",
            original_model="unknown-experimental",
            request=_make_payload(),
            messages=[],
            session=object(),
            ctx=_make_ctx(),
        )


@pytest.mark.asyncio
async def test_session_none_treats_all_candidates_as_keyless():
    """Defensive: session=None means we can't look up DB-stored keys.
    Every candidate should be skipped with ``no_api_key`` and the run
    must end with a ProviderError, not crash."""

    class _Dispatcher:
        def __init__(self):
            self.calls = 0

        async def dispatch(self, *, route, payload, messages, api_key):
            self.calls += 1
            return _ok_response(route.model_name)

    dispatcher = _Dispatcher()
    runner = FallbackOrchestrator(
        dispatcher=dispatcher,
        key_service=_FakeKeyService({"anthropic", "google", "openrouter"}),
    )
    with pytest.raises(GatewayError):
        await runner.run(
            original_provider="openai",
            original_model="gpt-4o",
            request=_make_payload(),
            messages=[],
            session=None,
            ctx=_make_ctx(),
        )
    assert dispatcher.calls == 0


# ---------------------------------------------------------------------------
# Regression: streaming pre-check must use the AWAITED async key lookup.
#
# Bug: the streaming fallback PRE-CHECK (public_chat.py) runs with the chat
# hot path's still-open AsyncSession. It called the SYNC
# ``select_streaming_candidate`` -> sync ``get_api_key`` ->
# ``AsyncSession.execute(...).scalars()``; since execute() on an AsyncSession
# returns a coroutine, this raised ``'coroutine' object has no attribute
# 'scalars'`` and 500'd — intermittently, only when a provider had just been
# marked unhealthy (so the pre-check fired). Fix added
# ``select_streaming_candidate_async`` which awaits ``get_api_key_async``.
# ---------------------------------------------------------------------------


class _StreamingAdapter:
    supports_streaming = True


class _AsyncOnlyKeyService:
    """The SYNC key path is unsafe with an AsyncSession (it would do an
    un-awaited execute), so it explodes here; only the awaited async path is
    valid. Mirrors the real failure mode the bug exposed."""

    def get_api_key(self, session, provider):  # noqa: D401
        raise AssertionError(
            "sync get_api_key must NOT be called on the async streaming "
            "pre-check path (it does an un-awaited AsyncSession.execute)"
        )

    async def get_api_key_async(self, session, provider):
        return f"sk-{provider}"


@pytest.mark.asyncio
async def test_streaming_candidate_async_uses_awaited_key_lookup():
    class _Dispatcher:
        adapters = {"anthropic": _StreamingAdapter()}

    runner = FallbackOrchestrator(
        dispatcher=_Dispatcher(),
        key_service=_AsyncOnlyKeyService(),
    )
    # ``session`` is a truthy stand-in for the open AsyncSession.
    candidate = await runner.select_streaming_candidate_async(
        original_provider="openai",
        original_model="gpt-4o",
        session=object(),
    )
    assert candidate is not None
    assert candidate["provider"] == "anthropic"
    assert candidate["api_key"] == "sk-anthropic"


@pytest.mark.asyncio
async def test_streaming_candidate_async_none_session_returns_none():
    class _Dispatcher:
        adapters = {"anthropic": _StreamingAdapter()}

    runner = FallbackOrchestrator(
        dispatcher=_Dispatcher(),
        key_service=_AsyncOnlyKeyService(),
    )
    assert (
        await runner.select_streaming_candidate_async(
            original_provider="openai",
            original_model="gpt-4o",
            session=None,
        )
        is None
    )
