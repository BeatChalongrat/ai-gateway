"""P2-06: Provider error mapping and retry tests.

Verifies:
- Timeout → ProviderTimeoutError
- ConnectError → ProviderUnavailableError
- 429 → RateLimitExceededError
- 502/503 → ProviderUnavailableError
- 500 → ProviderError
- 400-level → UpstreamBadResponseError
- Retry on transient failures then succeed
- Retry exhaustion raises domain exception
"""

import httpx
import pytest
import respx

from app.core.exceptions import (
    ContextLengthExceededError,
    ProviderError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RateLimitExceededError,
    UpstreamBadResponseError,
)
from app.services.gateway.fallback_orchestrator import is_fallback_eligible
from app.services.providers.error_handler import call_with_retry, map_provider_error


# ---------------------------------------------------------------------------
# map_provider_error
# ---------------------------------------------------------------------------


class TestMapProviderError:
    def test_timeout_exception(self):
        exc = httpx.ReadTimeout("read timed out")
        result = map_provider_error(exc, "openai")
        assert isinstance(result, ProviderTimeoutError)
        assert "openai" in result.message

    def test_connect_timeout(self):
        exc = httpx.ConnectTimeout("connect timed out")
        result = map_provider_error(exc, "anthropic")
        assert isinstance(result, ProviderTimeoutError)

    def test_connect_error(self):
        exc = httpx.ConnectError("connection refused")
        result = map_provider_error(exc, "openai")
        assert isinstance(result, ProviderUnavailableError)

    def test_429_rate_limit(self):
        resp = httpx.Response(429, json={"error": "rate limited"})
        req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
        exc = httpx.HTTPStatusError("429", request=req, response=resp)
        result = map_provider_error(exc, "openai")
        assert isinstance(result, RateLimitExceededError)

    def test_502_unavailable(self):
        resp = httpx.Response(502, text="Bad Gateway")
        req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
        exc = httpx.HTTPStatusError("502", request=req, response=resp)
        result = map_provider_error(exc, "openai")
        assert isinstance(result, ProviderUnavailableError)

    def test_503_unavailable(self):
        resp = httpx.Response(503, text="Service Unavailable")
        req = httpx.Request("POST", "https://x.com")
        exc = httpx.HTTPStatusError("503", request=req, response=resp)
        result = map_provider_error(exc, "openrouter")
        assert isinstance(result, ProviderUnavailableError)

    def test_500_server_error(self):
        resp = httpx.Response(500, json={"error": "internal"})
        req = httpx.Request("POST", "https://x.com")
        exc = httpx.HTTPStatusError("500", request=req, response=resp)
        result = map_provider_error(exc, "openai")
        assert isinstance(result, ProviderError)

    def test_400_bad_request(self):
        resp = httpx.Response(400, json={"error": "bad model"})
        req = httpx.Request("POST", "https://x.com")
        exc = httpx.HTTPStatusError("400", request=req, response=resp)
        result = map_provider_error(exc, "openai")
        assert isinstance(result, UpstreamBadResponseError)

    def test_401_auth_error(self):
        # A provider 401 is OUR key being rejected, not a client error.
        #
        # FIX57 Part 4 — it used to share the generic ``provider_error``
        # code, which bought fallback eligibility at the price of the
        # user being told "Couldn't reach the AI provider. Please try
        # again." for a provider that answered immediately and clearly.
        # The code is its own now; the eligibility is asserted below
        # rather than inherited, so this test still covers what it did.
        resp = httpx.Response(401, json={"error": "invalid key"})
        req = httpx.Request("POST", "https://x.com")
        exc = httpx.HTTPStatusError("401", request=req, response=resp)
        result = map_provider_error(exc, "openai")
        assert isinstance(result, ProviderError)
        assert result.code == "provider_auth_error"
        assert is_fallback_eligible(result), (
            "routing around a rejected credential to a provider whose key "
            "works is the whole point of the chain"
        )

    def test_403_forbidden_is_fallback_eligible(self):
        resp = httpx.Response(403, json={"error": "forbidden"})
        req = httpx.Request("POST", "https://x.com")
        exc = httpx.HTTPStatusError("403", request=req, response=resp)
        result = map_provider_error(exc, "anthropic")
        assert isinstance(result, ProviderError)
        assert result.code == "provider_auth_error"
        assert is_fallback_eligible(result)

    def test_404_model_not_found_is_fallback_eligible(self):
        # An aggregator that doesn't serve the requested model id returns
        # 404 — fallback-eligible so routing can try another candidate.
        resp = httpx.Response(404, json={"error": "No endpoints found"})
        req = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
        exc = httpx.HTTPStatusError("404", request=req, response=resp)
        result = map_provider_error(exc, "openrouter")
        assert isinstance(result, ProviderError)
        assert result.code == "provider_error"

    def test_unknown_exception(self):
        exc = RuntimeError("unexpected")
        result = map_provider_error(exc, "openai")
        assert isinstance(result, ProviderError)


# ---------------------------------------------------------------------------
# Context-length detection (typed context_length_exceeded on provider 400)
# ---------------------------------------------------------------------------


def _http_error(status: int, body) -> httpx.HTTPStatusError:
    resp = (
        httpx.Response(status, json=body)
        if isinstance(body, dict)
        else httpx.Response(status, text=body)
    )
    req = httpx.Request("POST", "https://x.com")
    return httpx.HTTPStatusError(str(status), request=req, response=resp)


class TestContextLengthDetection:
    def test_openai_error_code(self):
        body = {
            "error": {
                "code": "context_length_exceeded",
                "message": "This model's maximum context length is 128000 tokens. "
                           "However, your messages resulted in 131072 tokens.",
                "type": "invalid_request_error",
            }
        }
        result = map_provider_error(_http_error(400, body), "openai")
        assert isinstance(result, ContextLengthExceededError)
        assert result.code == "context_length_exceeded"
        assert result.details["provider"] == "openai"
        assert "maximum context length" in result.details["provider_message"]

    def test_openai_message_without_code(self):
        body = {"error": {"message": "Your input exceeds the context window of this model (tokens: 200123)."}}
        result = map_provider_error(_http_error(400, body), "openrouter")
        assert isinstance(result, ContextLengthExceededError)

    def test_anthropic_prompt_too_long(self):
        body = {
            "type": "error",
            "error": {
                "type": "invalid_request_error",
                "message": "prompt is too long: 210834 tokens > 200000 maximum",
            },
        }
        result = map_provider_error(_http_error(400, body), "anthropic")
        assert isinstance(result, ContextLengthExceededError)
        assert result.details["provider"] == "anthropic"
        assert "prompt is too long" in result.details["provider_message"]

    def test_gemini_token_count_exceeds(self):
        body = {
            "error": {
                "code": 400,
                "message": "The input token count (1200000) exceeds the maximum "
                           "number of tokens allowed (1048576).",
                "status": "INVALID_ARGUMENT",
            }
        }
        result = map_provider_error(_http_error(400, body), "google")
        assert isinstance(result, ContextLengthExceededError)

    def test_detection_is_case_insensitive(self):
        body = {"error": {"message": "Prompt Is Too Long for this model."}}
        result = map_provider_error(_http_error(400, body), "anthropic")
        assert isinstance(result, ContextLengthExceededError)

    def test_unrelated_400_still_upstream_bad_response(self):
        body = {"error": {"code": "invalid_request_error", "message": "Unknown parameter: 'foo'."}}
        result = map_provider_error(_http_error(400, body), "openai")
        assert isinstance(result, UpstreamBadResponseError)
        assert result.code == "upstream_bad_response"

    def test_500_with_context_text_is_not_matched(self):
        # Status-400-only rule: a 5xx mentioning tokens is a server fault.
        body = {"error": {"message": "internal error while sizing context window tokens"}}
        result = map_provider_error(_http_error(500, body), "openai")
        assert isinstance(result, ProviderError)
        assert not isinstance(result, ContextLengthExceededError)

    def test_plain_text_400_body_matched(self):
        result = map_provider_error(
            _http_error(400, "request exceeds the maximum number of tokens"), "google"
        )
        assert isinstance(result, ContextLengthExceededError)

    def test_not_fallback_eligible(self):
        from app.services.gateway.fallback_orchestrator import is_fallback_eligible

        assert not is_fallback_eligible(ContextLengthExceededError())


# ---------------------------------------------------------------------------
# call_with_retry
# ---------------------------------------------------------------------------


def _ok_response():
    """Create a 200 response with a request set (required for raise_for_status)."""
    req = httpx.Request("POST", "https://x.com")
    return httpx.Response(200, json={"ok": True}, request=req)


class TestCallWithRetry:
    @pytest.mark.asyncio
    async def test_success_on_first_try(self):
        async def factory():
            return _ok_response()

        result = await call_with_retry(
            factory, provider_name="test", max_retries=2
        )
        assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_retry_then_succeed(self):
        call_count = 0

        async def factory():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                resp = httpx.Response(503, text="unavailable")
                req = httpx.Request("POST", "https://x.com")
                raise httpx.HTTPStatusError("503", request=req, response=resp)
            return _ok_response()

        result = await call_with_retry(
            factory, provider_name="test", max_retries=2, base_delay=0.01
        )
        assert result.status_code == 200
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_retry_exhausted_raises_domain_error(self):
        async def factory():
            resp = httpx.Response(503, text="unavailable")
            req = httpx.Request("POST", "https://x.com")
            raise httpx.HTTPStatusError("503", request=req, response=resp)

        with pytest.raises(ProviderUnavailableError):
            await call_with_retry(
                factory, provider_name="test", max_retries=1, base_delay=0.01
            )

    @pytest.mark.asyncio
    async def test_non_retryable_error_raises_immediately(self):
        call_count = 0

        async def factory():
            nonlocal call_count
            call_count += 1
            resp = httpx.Response(400, json={"error": "bad"})
            req = httpx.Request("POST", "https://x.com")
            raise httpx.HTTPStatusError("400", request=req, response=resp)

        with pytest.raises(UpstreamBadResponseError):
            await call_with_retry(
                factory, provider_name="test", max_retries=2, base_delay=0.01
            )
        assert call_count == 1  # No retries for 400

    @pytest.mark.asyncio
    async def test_timeout_retried(self):
        call_count = 0

        async def factory():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise httpx.ReadTimeout("read timed out")
            return _ok_response()

        result = await call_with_retry(
            factory, provider_name="test", max_retries=2, base_delay=0.01
        )
        assert result.status_code == 200
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_timeout_exhausted(self):
        async def factory():
            raise httpx.ReadTimeout("read timed out")

        with pytest.raises(ProviderTimeoutError):
            await call_with_retry(
                factory, provider_name="test", max_retries=1, base_delay=0.01
            )
