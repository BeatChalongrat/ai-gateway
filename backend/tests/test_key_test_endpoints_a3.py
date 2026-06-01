"""Group A / A3 — provider key-test endpoints must require auth.

A test endpoint that hits a PUBLIC route (OpenRouter's ``/api/v1/models``)
returns 200 for any string, so a placeholder key tests as "valid" — the
exact lie that lets a dead key into the pool. Every provider's test probe
must hit an auth-required endpoint so a bad key comes back invalid.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.services.provider_key_service import ProviderKeyService


@pytest.mark.asyncio
@respx.mock
async def test_openrouter_invalid_key_reports_invalid():
    # The auth-required endpoint 401s on a bad key.
    route = respx.get("https://openrouter.ai/api/v1/auth/key").mock(
        return_value=httpx.Response(
            401, json={"error": {"message": "No auth credentials found"}}
        )
    )
    svc = ProviderKeyService()
    result = await svc.test_key("openrouter", "sk-or-placeholder")

    assert route.called
    assert result["valid"] is False
    assert "auth" in result["error"].lower()


@pytest.mark.asyncio
@respx.mock
async def test_openrouter_valid_key_reports_valid():
    respx.get("https://openrouter.ai/api/v1/auth/key").mock(
        return_value=httpx.Response(200, json={"data": {"label": "k"}})
    )
    svc = ProviderKeyService()
    result = await svc.test_key("openrouter", "sk-or-realkey")
    assert result["valid"] is True


@pytest.mark.asyncio
@respx.mock
async def test_openrouter_does_not_use_public_models_endpoint():
    # Pin the public endpoint so a regression that points the probe back at
    # it is caught: it must NOT be called.
    public = respx.get("https://openrouter.ai/api/v1/models").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    respx.get("https://openrouter.ai/api/v1/auth/key").mock(
        return_value=httpx.Response(401, json={"error": {"message": "bad"}})
    )
    svc = ProviderKeyService()
    result = await svc.test_key("openrouter", "placeholder")

    assert not public.called, "must not validate via the public models route"
    assert result["valid"] is False


@pytest.mark.asyncio
@respx.mock
async def test_openai_invalid_key_reports_invalid():
    # OpenAI's /v1/models already requires auth — confirm a 401 is invalid.
    respx.get("https://api.openai.com/v1/models").mock(
        return_value=httpx.Response(
            401, json={"error": {"message": "Incorrect API key"}}
        )
    )
    svc = ProviderKeyService()
    result = await svc.test_key("openai", "sk-bad")
    assert result["valid"] is False


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_invalid_key_reports_invalid():
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            401, json={"error": {"message": "invalid x-api-key"}}
        )
    )
    svc = ProviderKeyService()
    result = await svc.test_key("anthropic", "sk-ant-bad")
    assert result["valid"] is False
