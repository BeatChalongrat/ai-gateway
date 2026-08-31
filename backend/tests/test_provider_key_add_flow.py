"""Regression coverage for admin provider-key validation and persistence."""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
import respx

from app.api.routes.admin import add_provider_key
from app.core.exceptions import ConflictError
from app.repositories.provider_key_repository import ProviderKeyRepository
from app.services.provider_key_service import (
    ProviderKeyService,
    _decode,
)


TEST_KEY = "sk-or-v1-" + "a" * 64
REPLACEMENT_KEY = "sk-or-v1-" + "b" * 64


def test_add_key_reuses_soft_deleted_label(db_session):
    svc = ProviderKeyService()
    original = svc.add_key(db_session, "openrouter", TEST_KEY, label="admin")
    svc.update_test_result(db_session, original.id, "valid", 10)
    svc.delete_key(db_session, original.id)

    replacement = svc.add_key(
        db_session, "openrouter", REPLACEMENT_KEY, label="admin"
    )

    assert replacement.id == original.id
    assert replacement.is_active is True
    assert replacement.last_test_status == "untested"
    assert replacement.last_tested_at is None
    assert _decode(replacement.api_key_encrypted) == REPLACEMENT_KEY
    assert len(ProviderKeyRepository(db_session).list_by_provider("openrouter")) == 1


def test_add_key_rejects_duplicate_active_label(db_session):
    svc = ProviderKeyService()
    svc.add_key(db_session, "openrouter", TEST_KEY, label="admin")

    with pytest.raises(ConflictError) as exc_info:
        svc.add_key(db_session, "openrouter", REPLACEMENT_KEY, label="admin")

    assert exc_info.value.code == "provider_key_label_conflict"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "url"),
    [
        ("deepseek", "https://api.deepseek.com/v1/models"),
        ("groq", "https://api.groq.com/openai/v1/models"),
        ("kimi", "https://api.moonshot.cn/v1/models"),
        ("minimax", "https://api.minimax.chat/v1/models"),
        ("qwen", "https://dashscope.aliyuncs.com/compatible-mode/v1/models"),
        ("xai", "https://api.x.ai/v1/models"),
        ("zai", "https://api.01.ai/v1/models"),
    ],
)
@respx.mock
async def test_registered_openai_compatible_providers_have_key_probes(
    provider, url
):
    route = respx.get(url).mock(return_value=httpx.Response(200, json={"data": []}))

    result = await ProviderKeyService().test_key(provider, TEST_KEY)

    assert route.called
    assert result["valid"] is True
    assert result["provider_status"] == 200


@pytest.mark.asyncio
@respx.mock
async def test_google_probe_uses_header_not_query_string():
    route = respx.get(
        "https://generativelanguage.googleapis.com/v1beta/models"
    ).mock(return_value=httpx.Response(200, json={"models": []}))

    result = await ProviderKeyService().test_key("google", TEST_KEY)

    assert result["valid"] is True
    request = route.calls.last.request
    assert request.headers["x-goog-api-key"] == TEST_KEY
    assert "key=" not in str(request.url)


@pytest.mark.asyncio
async def test_pending_provider_reports_adapter_not_implemented():
    result = await ProviderKeyService().test_key("mistral", TEST_KEY)

    assert result["valid"] is False
    assert result["error_type"] == "not_implemented"
    assert "adapter is not implemented" in result["error"]


@pytest.mark.asyncio
@respx.mock
async def test_openrouter_timeout_is_classified_without_secret_text():
    respx.get("https://openrouter.ai/api/v1/key").mock(
        side_effect=httpx.ReadTimeout("request timed out")
    )

    result = await ProviderKeyService().test_key("openrouter", TEST_KEY)

    assert result["valid"] is False
    assert result["error_type"] == "timeout"
    assert result["provider_status"] is None
    assert TEST_KEY not in result["error"]


async def _valid_test_key(self, provider_name, api_key, *, base_url=None):
    return {
        "valid": True,
        "latency_ms": 12,
        "error": None,
        "error_type": None,
        "provider_status": 200,
    }


@pytest.mark.asyncio
async def test_add_route_saves_after_provider_200(db_session, monkeypatch):
    monkeypatch.setattr(ProviderKeyService, "test_key", _valid_test_key)
    label = f"route-success-{uuid.uuid4().hex}"

    result = await add_provider_key(
        "elevenlabs",
        {"api_key": TEST_KEY, "label": label},
        db_session,
    )

    assert result["valid"] is True
    rows = ProviderKeyRepository(db_session).list_by_provider("elevenlabs")
    assert any(row.label == label and row.last_test_status == "valid" for row in rows)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_error", "error_type", "provider_status"),
    [
        ("provider returned HTTP 401: invalid token", "authentication", 401),
        ("connection timed out while validating key", "timeout", None),
    ],
)
async def test_add_route_explains_validation_failure_without_saving(
    db_session,
    monkeypatch,
    provider_error,
    error_type,
    provider_status,
):
    async def rejected(self, provider_name, api_key, *, base_url=None):
        return {
            "valid": False,
            "latency_ms": 5,
            "error": provider_error,
            "error_type": error_type,
            "provider_status": provider_status,
        }

    monkeypatch.setattr(ProviderKeyService, "test_key", rejected)
    label = f"route-rejected-{uuid.uuid4().hex}"
    before = len(ProviderKeyRepository(db_session).list_by_provider("elevenlabs"))

    response = await add_provider_key(
        "elevenlabs",
        {"api_key": TEST_KEY, "label": label},
        db_session,
    )
    body = json.loads(response.body)

    assert response.status_code == 400
    assert body["stage"] == "validate"
    assert provider_error in body["error"]["message"]
    after = len(ProviderKeyRepository(db_session).list_by_provider("elevenlabs"))
    assert after == before


@pytest.mark.asyncio
async def test_add_route_returns_safe_save_error(db_session, monkeypatch):
    monkeypatch.setattr(ProviderKeyService, "test_key", _valid_test_key)

    def broken_save(self, session, provider_name, api_key, label=None):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(ProviderKeyService, "add_key", broken_save)
    response = await add_provider_key(
        "elevenlabs",
        {"api_key": TEST_KEY, "label": "save-failure"},
        db_session,
    )
    body = json.loads(response.body)

    assert response.status_code == 500
    assert body["stage"] == "save"
    assert body["error"]["code"] == "provider_key_save_failed"
    assert TEST_KEY not in response.body.decode()
