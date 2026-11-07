"""P5-06: Fallback routing strategy tests.

Verifies the Layer-1 ``dispatch_with_fallback`` semantics:
- Primary success returns immediately, no fallback attempted
- Fallback-eligible primary failure tries the first Layer-1 hop
- All candidates exhausted raises the LAST fallback-eligible error
- Fallback is bounded by ``max_hops`` — no infinite loops
- NON-fallback-eligible errors (e.g. plain ``ProviderError`` without
  a transient code) propagate immediately without consuming hops
"""

import pytest

from app.core.exceptions import (
    ProviderError,
    ProviderUnavailableError,
    ProviderTimeoutError,
)
from app.domain.entities import RouteDecision
from app.domain.providers import ProviderResponse
from app.schemas.common import Message
from app.schemas.public import ChatCompletionsRequest
from app.services.provider_dispatcher import ProviderDispatcher


# The autouse fixture in ``conftest.py`` stubs ``ProviderDispatcher.
# dispatch_with_fallback`` for every test by default. This file tests
# the REAL ``dispatch_with_fallback``, so we override the fixture
# locally with a no-op that lets each test monkeypatch the underlying
# adapters directly.
@pytest.fixture(autouse=True)
def _mock_provider_dispatch():  # pragma: no cover — fixture wiring
    yield


def _payload():
    return ChatCompletionsRequest(
        model="gpt-4o",
        messages=[Message(role="user", content="hi")],
    )


def _messages():
    return [Message(role="user", content="hi")]


def _route(primary="openai", fallbacks=None):
    return RouteDecision(
        provider_name=primary,
        model_name="m1",
        fallbacks=[(p, f"m_{p}") for p in (fallbacks or [])],
    )


class TestLayer1Dispatch:
    @pytest.mark.asyncio
    async def test_primary_success(self):
        dispatcher = ProviderDispatcher()
        call_log: list[str] = []

        async def mock_chat(self, payload, messages, *, resolved_model=None, api_key=None):
            call_log.append(self.provider_name)
            return ProviderResponse(content="ok", model=resolved_model or "m")

        for adapter in dispatcher.adapters.values():
            adapter.chat = mock_chat.__get__(adapter)

        resp, used_provider, used_model = await dispatcher.dispatch_with_fallback(
            _route("openai", ["anthropic"]),
            _payload(),
            _messages(),
            api_key="primary-key",
            key_resolver=lambda p, m=None: f"key-{p}",
        )
        assert resp.content == "ok"
        assert used_provider == "openai"
        assert used_model == "m1"
        assert call_log == ["openai"]

    @pytest.mark.asyncio
    async def test_fallback_on_primary_eligible_failure(self):
        dispatcher = ProviderDispatcher()
        call_log: list[str] = []

        async def chat(self, payload, messages, *, resolved_model=None, api_key=None):
            call_log.append(self.provider_name)
            if self.provider_name == "openai":
                raise ProviderUnavailableError("openai down")
            return ProviderResponse(content="fallback ok", model="m")

        for adapter in dispatcher.adapters.values():
            adapter.chat = chat.__get__(adapter)

        resp, used_provider, used_model = await dispatcher.dispatch_with_fallback(
            _route("openai", ["anthropic"]),
            _payload(),
            _messages(),
            api_key="primary-key",
            key_resolver=lambda p, m=None: f"key-{p}",
        )
        assert resp.content == "fallback ok"
        assert used_provider == "anthropic"
        assert call_log == ["openai", "anthropic"]

    @pytest.mark.asyncio
    async def test_non_eligible_provider_error_subclass_does_not_fallback(self):
        # ``ProviderNotImplementedError`` (code=``provider_not_implemented``)
        # is a ``ProviderError`` subclass but the code is NOT in
        # ``FALLBACK_ELIGIBLE_ERRORS``. Should re-raise immediately
        # without trying the fallback list — retrying a 4xx on a
        # different provider would just burn quota on the same bad
        # input.
        dispatcher = ProviderDispatcher()
        call_log: list[str] = []

        async def chat(self, payload, messages, *, resolved_model=None, api_key=None):
            from app.core.exceptions import ProviderNotImplementedError
            call_log.append(self.provider_name)
            raise ProviderNotImplementedError("not wired up")

        for adapter in dispatcher.adapters.values():
            adapter.chat = chat.__get__(adapter)

        from app.core.exceptions import ProviderNotImplementedError
        with pytest.raises(ProviderNotImplementedError):
            await dispatcher.dispatch_with_fallback(
                _route("openai", ["anthropic", "openrouter"]),
                _payload(),
                _messages(),
                api_key="primary-key",
                key_resolver=lambda p, m=None: f"key-{p}",
            )
        # Only the primary was tried — non-eligible code short-circuits.
        assert call_log == ["openai"]

    @pytest.mark.asyncio
    async def test_all_eligible_failures_raise_last(self):
        dispatcher = ProviderDispatcher()

        async def always_fail(self, payload, messages, *, resolved_model=None, api_key=None):
            raise ProviderTimeoutError(f"{self.provider_name} timed out")

        for adapter in dispatcher.adapters.values():
            adapter.chat = always_fail.__get__(adapter)

        with pytest.raises(ProviderTimeoutError):
            await dispatcher.dispatch_with_fallback(
                _route("openai", ["anthropic"]),
                _payload(),
                _messages(),
                api_key="primary-key",
                key_resolver=lambda p, m=None: f"key-{p}",
            )

    @pytest.mark.asyncio
    async def test_max_hops_caps_attempts(self):
        # max_hops=2 → primary + 2 hops = 3 total attempts max,
        # even if more fallback candidates are listed.
        dispatcher = ProviderDispatcher()
        call_count = 0

        async def counting_fail(self, payload, messages, *, resolved_model=None, api_key=None):
            nonlocal call_count
            call_count += 1
            raise ProviderUnavailableError(f"{self.provider_name} down")

        for adapter in dispatcher.adapters.values():
            adapter.chat = counting_fail.__get__(adapter)

        with pytest.raises(ProviderUnavailableError):
            await dispatcher.dispatch_with_fallback(
                _route("openai", ["anthropic", "openrouter", "google"]),
                _payload(),
                _messages(),
                api_key="primary-key",
                key_resolver=lambda p, m=None: f"key-{p}",
                max_hops=2,
            )
        # Exactly 3 attempts: primary + 2 hops.
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_missing_key_skips_without_consuming_hop(self):
        # Operator-friendly rule (mirrors Layer-2): a fallback hop
        # whose provider has no configured API key is skipped and
        # does NOT consume one of the ``max_hops`` slots.
        dispatcher = ProviderDispatcher()
        call_log: list[str] = []

        async def chat(self, payload, messages, *, resolved_model=None, api_key=None):
            call_log.append(self.provider_name)
            if self.provider_name == "openai":
                raise ProviderUnavailableError("openai down")
            return ProviderResponse(content="recovered", model="m")

        for adapter in dispatcher.adapters.values():
            adapter.chat = chat.__get__(adapter)

        # ``anthropic`` has no key (resolver returns None); ``openrouter``
        # does. The hop counter should still allow the openrouter try.
        def resolver(provider: str, model: str | None = None) -> str | None:
            return None if provider == "anthropic" else f"key-{provider}"

        resp, used_provider, _ = await dispatcher.dispatch_with_fallback(
            _route("openai", ["anthropic", "openrouter"]),
            _payload(),
            _messages(),
            api_key="primary-key",
            key_resolver=resolver,
            max_hops=2,
        )
        assert resp.content == "recovered"
        assert used_provider == "openrouter"
        assert "anthropic" not in call_log
