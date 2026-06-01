"""Group C / C2 — model health probe + log-only new-model discovery."""

from __future__ import annotations

import pytest

from app.domain.providers import ProviderResponse
from app.services.model_health import check_model_health
from app.services.provider_dispatcher import ProviderDispatcher


@pytest.mark.asyncio
async def test_health_probe_healthy_on_success(monkeypatch, db_session):
    async def _ok(self, *, route, payload, messages, api_key=None):
        assert payload.max_tokens == 1, "probe must be a 1-token request"
        return ProviderResponse(content="x", model=route.model_name)

    monkeypatch.setattr(ProviderDispatcher, "dispatch", _ok)
    monkeypatch.setattr(
        "app.services.provider_key_service.ProviderKeyService.get_active_key",
        lambda self, s, p: "k",
    )

    result = await check_model_health(db_session, "openai", "gpt-4o")
    assert result["healthy"] is True
    assert result["error"] is None


@pytest.mark.asyncio
async def test_health_probe_unhealthy_on_provider_error(monkeypatch, db_session):
    from app.core.exceptions import ProviderError

    async def _fail(self, *, route, payload, messages, api_key=None):
        raise ProviderError("model not found (404)", details={"status": 404})

    monkeypatch.setattr(ProviderDispatcher, "dispatch", _fail)
    monkeypatch.setattr(
        "app.services.provider_key_service.ProviderKeyService.get_active_key",
        lambda self, s, p: "k",
    )

    result = await check_model_health(db_session, "openai", "ghost-model")
    assert result["healthy"] is False
    assert "404" in result["error"]


@pytest.mark.asyncio
async def test_health_probe_no_key_is_unhealthy_not_raised(monkeypatch, db_session):
    monkeypatch.setattr(
        "app.services.provider_key_service.ProviderKeyService.get_active_key",
        lambda self, s, p: None,
    )
    result = await check_model_health(db_session, "openai", "gpt-4o")
    assert result["healthy"] is False
    assert "key" in result["error"].lower()
