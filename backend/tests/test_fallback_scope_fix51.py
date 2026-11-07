"""FIX51 Part 4 (P1-F) — chains span accounts; failure scope decides depth;
the ceiling is time.

Three behaviors pinned here, one per section:

* every curated chain spans at least two provider ACCOUNTS — the
  FIX48-era table sent every hop through OpenRouter ("the provider
  holding a live key"), so the day that one account ran dry BOTH rescue
  hops died with it;
* the failure's SCOPE (model / account / request) decides how the chain
  walks: an account-level failure (bare 402, rejected credentials, 5xx)
  never retries the same account, and a request-level failure (context
  too long) stops the walk immediately;
* the total wait is capped in seconds, not attempts — a primary that
  burned the whole budget surfaces its error now instead of after two
  more doomed 10s tries.
"""

from __future__ import annotations

import time as _time
from unittest.mock import MagicMock

import pytest

from app.core.exceptions import (
    ContextLengthExceededError,
    ProviderError,
)
from app.domain.entities import RequestContext
from app.domain.providers import ProviderResponse
from app.services.gateway.fallback_orchestrator import (
    FallbackOrchestrator,
    classify_failure_scope,
    is_fallback_eligible,
)


def _make_ctx() -> RequestContext:
    return RequestContext(
        tenant_id="tenant-x",
        project_id="proj-x",
        project_name="Test",
        request_id="req_fix51",
    )


def _make_payload() -> object:
    p = MagicMock()
    p.model = "gpt-4o"
    p.metadata = {}
    return p


def _ok_response(model: str) -> ProviderResponse:
    return ProviderResponse(content="ok", model=model, finish_reason="stop")


class _FakeKeyService:
    def __init__(self, keys_for: set[str]) -> None:
        self.keys_for = keys_for

    def get_api_key(self, session, provider: str, model: str | None = None) -> str | None:
        return f"sk-{provider}" if provider in self.keys_for else None

    async def get_api_key_async(self, session, provider: str, model: str | None = None) -> str | None:
        return self.get_api_key(session, provider)


def _no_credit(provider: str = "openrouter") -> ProviderError:
    return ProviderError(
        f"Provider '{provider}' reported insufficient credits (402).",
        code="provider_no_credit",
        details={"provider": provider, "status": 402},
    )


def _patched_chain(monkeypatch, chain):
    import app.services.gateway.fallback_orchestrator as fo

    monkeypatch.setattr(fo, "get_fallback_chain", lambda m, p: list(chain))


# ── chains span accounts ────────────────────────────────────────────────


def test_every_curated_chain_spans_two_accounts():
    from app.services.providers.model_tiers import CURATED_FALLBACKS

    assert CURATED_FALLBACKS, "curated table went missing"
    for alias, chain in CURATED_FALLBACKS.items():
        providers = {c["provider"] for c in chain}
        assert len(providers) >= 2, (
            f"curated chain for {alias!r} lives entirely on {providers} "
            "— one dead account kills every hop"
        )


# ── failure-scope classifier ────────────────────────────────────────────


def test_classify_402_no_credit_as_account_scope():
    exc = _no_credit()
    assert classify_failure_scope(exc) == "account"
    # And it stays fallback-eligible — another ACCOUNT can answer.
    assert is_fallback_eligible(exc) is True


def test_classify_auth_status_as_account_scope():
    exc = ProviderError(
        "Provider rejected credentials (401).",
        details={"provider": "openai", "status": 401},
    )
    assert classify_failure_scope(exc) == "account"


def test_classify_404_as_model_scope():
    exc = ProviderError(
        "no endpoint for model",
        details={"provider": "openrouter", "status": 404},
    )
    assert classify_failure_scope(exc) == "model"


def test_classify_context_length_as_request_scope():
    assert classify_failure_scope(ContextLengthExceededError()) == "request"


def test_classify_unknown_defaults_to_model_scope():
    assert classify_failure_scope(ProviderError("mystery")) == "model"


# ── run(): scope-aware walk ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_account_failure_skips_same_provider_sibling(monkeypatch):
    """A no-credit failure on openrouter must NOT try openrouter again."""

    _patched_chain(
        monkeypatch,
        [
            {"provider": "openrouter", "model": "m1"},
            {"provider": "openrouter", "model": "m2"},
            {"provider": "deepseek", "model": "m3"},
        ],
    )

    class _Dispatcher:
        def __init__(self):
            self.calls = []

        async def dispatch(self, *, route, payload, messages, api_key):
            self.calls.append((route.provider_name, route.model_name))
            if route.provider_name == "openrouter":
                raise _no_credit()
            return _ok_response(route.model_name)

    dispatcher = _Dispatcher()
    runner = FallbackOrchestrator(
        dispatcher=dispatcher,
        key_service=_FakeKeyService({"openrouter", "deepseek"}),
    )
    result = await runner.run(
        original_provider="openai",
        original_model="gpt-4o",
        request=_make_payload(),
        messages=[],
        session=object(),
        ctx=_make_ctx(),
    )

    assert dispatcher.calls == [("openrouter", "m1"), ("deepseek", "m3")]
    assert result.provider_used == "deepseek"


@pytest.mark.asyncio
async def test_primary_account_failure_excludes_that_account(monkeypatch):
    """The PRIMARY's no-credit failure rules its account out of the chain."""

    _patched_chain(
        monkeypatch,
        [
            {"provider": "openrouter", "model": "m1"},
            {"provider": "deepseek", "model": "m2"},
        ],
    )

    class _Dispatcher:
        def __init__(self):
            self.calls = []

        async def dispatch(self, *, route, payload, messages, api_key):
            self.calls.append((route.provider_name, route.model_name))
            return _ok_response(route.model_name)

    dispatcher = _Dispatcher()
    runner = FallbackOrchestrator(
        dispatcher=dispatcher,
        key_service=_FakeKeyService({"openrouter", "deepseek"}),
    )
    result = await runner.run(
        original_provider="openrouter",
        original_model="openai/gpt-4o",
        request=_make_payload(),
        messages=[],
        session=object(),
        ctx=_make_ctx(),
        original_error=_no_credit(),
    )

    assert dispatcher.calls == [("deepseek", "m2")]
    assert result.provider_used == "deepseek"


@pytest.mark.asyncio
async def test_request_scoped_failure_stops_the_walk(monkeypatch):
    """Context-too-long fails everywhere — one try, then surface it."""

    _patched_chain(
        monkeypatch,
        [
            {"provider": "anthropic", "model": "m1"},
            {"provider": "deepseek", "model": "m2"},
        ],
    )

    class _Dispatcher:
        def __init__(self):
            self.calls = []

        async def dispatch(self, *, route, payload, messages, api_key):
            self.calls.append((route.provider_name, route.model_name))
            raise ContextLengthExceededError(
                details={"provider": route.provider_name}
            )

    dispatcher = _Dispatcher()
    runner = FallbackOrchestrator(
        dispatcher=dispatcher,
        key_service=_FakeKeyService({"anthropic", "deepseek"}),
    )
    with pytest.raises(ContextLengthExceededError):
        await runner.run(
            original_provider="openai",
            original_model="gpt-4o",
            request=_make_payload(),
            messages=[],
            session=object(),
            ctx=_make_ctx(),
        )

    assert dispatcher.calls == [("anthropic", "m1")]


# ── run(): time budget ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_time_budget_opens_no_attempt_when_spent(monkeypatch):
    _patched_chain(
        monkeypatch,
        [
            {"provider": "anthropic", "model": "m1"},
            {"provider": "deepseek", "model": "m2"},
        ],
    )

    class _Dispatcher:
        def __init__(self):
            self.calls = []

        async def dispatch(self, *, route, payload, messages, api_key):
            self.calls.append((route.provider_name, route.model_name))
            return _ok_response(route.model_name)

    dispatcher = _Dispatcher()
    runner = FallbackOrchestrator(
        dispatcher=dispatcher,
        key_service=_FakeKeyService({"anthropic", "deepseek"}),
    )
    with pytest.raises(ProviderError):
        await runner.run(
            original_provider="openai",
            original_model="gpt-4o",
            request=_make_payload(),
            messages=[],
            session=object(),
            ctx=_make_ctx(),
            # The turn started 60s ago — the 25s budget is long gone.
            started_at=_time.perf_counter() - 60.0,
        )

    assert dispatcher.calls == []


@pytest.mark.asyncio
async def test_time_budget_absent_keeps_old_behavior(monkeypatch):
    """No started_at → the budget covers the walk alone: attempts open."""

    _patched_chain(monkeypatch, [{"provider": "deepseek", "model": "m2"}])

    class _Dispatcher:
        def __init__(self):
            self.calls = []

        async def dispatch(self, *, route, payload, messages, api_key):
            self.calls.append((route.provider_name, route.model_name))
            return _ok_response(route.model_name)

    dispatcher = _Dispatcher()
    runner = FallbackOrchestrator(
        dispatcher=dispatcher, key_service=_FakeKeyService({"deepseek"})
    )
    result = await runner.run(
        original_provider="openai",
        original_model="gpt-4o",
        request=_make_payload(),
        messages=[],
        session=object(),
        ctx=_make_ctx(),
    )
    assert result.provider_used == "deepseek"
