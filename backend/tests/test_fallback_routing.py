"""P5-06: Fallback routing strategy tests.

Verifies:
- Primary success returns immediately
- Primary failure tries first fallback
- All candidates exhausted raises last error
- Fallback is bounded (no infinite loops)
"""

import pytest

from app.core.exceptions import ProviderError, ProviderUnavailableError
from app.domain.providers import ProviderResponse
from app.domain.routing import ModelCandidate, RoutingResult
from app.schemas.common import Message
from app.schemas.public import ChatCompletionsRequest
from app.services.provider_dispatcher import ProviderDispatcher


def _payload():
    return ChatCompletionsRequest(
        model="gpt-4o",
        messages=[Message(role="user", content="hi")],
    )


def _messages():
    return [Message(role="user", content="hi")]


def _result(selected_provider="openai", fallback_providers=None):
    selected = ModelCandidate(provider=selected_provider, model="m1")
    fallbacks = [
        ModelCandidate(provider=p, model=f"m_{p}")
        for p in (fallback_providers or [])
    ]
    return RoutingResult(selected=selected, fallbacks=fallbacks)


class TestFallbackDispatch:
    @pytest.mark.asyncio
    async def test_primary_success(self):
        dispatcher = ProviderDispatcher()
        # Stub adapter returns ProviderResponse (conftest monkeypatches dispatch)
        # But dispatch_with_fallback calls adapter.chat directly, so we need
        # to monkeypatch the adapters
        call_log = []

        async def mock_chat(self, payload, messages, *, resolved_model=None):
            call_log.append(self.provider_name)
            return ProviderResponse(content="ok", model=resolved_model or "m")

        for adapter in dispatcher.adapters.values():
            adapter.chat = mock_chat.__get__(adapter)

        result = _result("openai", ["anthropic"])
        resp, candidate = await dispatcher.dispatch_with_fallback(
            result, _payload(), _messages()
        )
        assert resp.content == "ok"
        assert candidate.provider == "openai"
        assert call_log == ["openai"]

    @pytest.mark.asyncio
    async def test_fallback_on_primary_failure(self):
        dispatcher = ProviderDispatcher()
        call_log = []

        async def failing_chat(self, payload, messages, *, resolved_model=None):
            call_log.append(self.provider_name)
            if self.provider_name == "openai":
                raise ProviderUnavailableError("openai down")
            return ProviderResponse(content="fallback ok", model="m")

        for adapter in dispatcher.adapters.values():
            adapter.chat = failing_chat.__get__(adapter)

        result = _result("openai", ["anthropic"])
        resp, candidate = await dispatcher.dispatch_with_fallback(
            result, _payload(), _messages()
        )
        assert resp.content == "fallback ok"
        assert candidate.provider == "anthropic"
        assert call_log == ["openai", "anthropic"]

    @pytest.mark.asyncio
    async def test_all_fail_raises(self):
        dispatcher = ProviderDispatcher()

        async def always_fail(self, payload, messages, *, resolved_model=None):
            raise ProviderUnavailableError(f"{self.provider_name} down")

        for adapter in dispatcher.adapters.values():
            adapter.chat = always_fail.__get__(adapter)

        result = _result("openai", ["anthropic"])
        with pytest.raises(ProviderUnavailableError):
            await dispatcher.dispatch_with_fallback(
                result, _payload(), _messages()
            )

    @pytest.mark.asyncio
    async def test_bounded_no_extra_attempts(self):
        dispatcher = ProviderDispatcher()
        call_count = 0

        async def counting_fail(self, payload, messages, *, resolved_model=None):
            nonlocal call_count
            call_count += 1
            raise ProviderError("fail")

        for adapter in dispatcher.adapters.values():
            adapter.chat = counting_fail.__get__(adapter)

        result = _result("openai", ["anthropic", "openrouter"])
        with pytest.raises(ProviderError):
            await dispatcher.dispatch_with_fallback(
                result, _payload(), _messages()
            )
        # Exactly 3 attempts: primary + 2 fallbacks
        assert call_count == 3
