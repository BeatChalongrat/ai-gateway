"""FIX67 — POST /v1/images/generations walks the destination order.

Both clients (web via chat-edge, desktop direct) hit this one endpoint, so a
fallback proven here is proven for both paths. Driven with respx so OpenAI's
response is controlled:

  * primary openai fails (429 insufficient_quota, the owner's real condition) →
    the mock second tier produces → the response is attributed to mock;  [cell 2]
  * primary openai succeeds → the gpt-image-1 path is byte-for-byte unchanged,
    attributed to openai;                                                 [cell 4]
  * mock is in the order but media_allow_mock is off → mock is NOT used, the
    request fails at openai — mock never leaks to a real user.            [cell 6]
"""

import httpx
import pytest
import pytest_asyncio
import respx
from starlette.testclient import TestClient

import app.api.routes.public_images as public_images
from app.core.config import settings
from app.main import app as main_app
from app.models.system_setting import SystemSettingORM
from app.services import provider_key_service as pks_mod
from app.services.gateway import portal_credit_check as credit_mod

_IMG_URL = "https://api.openai.com/v1/images/generations"


async def _no_valid(self, session):  # providers_with_valid_keys_async
    return {"openai"}


async def _key(self, session, provider):  # get_active_key_async
    return "sk-test-key"


async def _noop(*a, **k):
    return None


@pytest.fixture
def wire(monkeypatch):
    """Neutralise rate-limit / credit / DLP so the test isolates the walker."""
    monkeypatch.setattr(pks_mod.ProviderKeyService, "providers_with_valid_keys_async", _no_valid)
    monkeypatch.setattr(pks_mod.ProviderKeyService, "get_active_key_async", _key)
    monkeypatch.setattr(public_images, "_enforce_image_rate_limits", _noop)
    monkeypatch.setattr(credit_mod, "check_portal_credit_async", _noop)
    monkeypatch.setattr(credit_mod, "deduct_portal_credit_async", _noop)
    monkeypatch.setattr(settings, "dlp_enabled", False)
    # Fail the primary fast — no retry backoff on the 429.
    monkeypatch.setattr(settings, "provider_max_retries", 0)


# No lifespan: a bare TestClient does not run the app's startup/shutdown
# (background job loops), which would otherwise hang the test.
client = TestClient(main_app)


@pytest_asyncio.fixture(autouse=True)
async def _cleanup_media_settings(async_db):
    """The integration DB is a shared file; committed media_* settings would
    otherwise leak into every later image test (flipping the order / mock
    switch). Remove them after each test so the legacy openai-only path is
    restored for everyone else."""
    yield
    for key in ("media_order:image", "media_order:video", "media_allow_mock"):
        row = await async_db.get(SystemSettingORM, key)
        if row is not None:
            await async_db.delete(row)
    await async_db.commit()


async def _set_order(async_db, image_order: str, allow_mock: str):
    for key, val in (
        ("media_order:image", image_order),
        ("media_allow_mock", allow_mock),
    ):
        row = await async_db.get(SystemSettingORM, key)
        if row is None:
            async_db.add(SystemSettingORM(key=key, value=val))
        else:
            row.value = val
    await async_db.commit()


_OK_IMAGE = {
    "created": 1699000000,
    "data": [{"b64_json": "aGVsbG8=", "revised_prompt": "a small orange cat"}],
}
_QUOTA_ERR = {"error": {"code": "insufficient_quota", "type": "insufficient_quota", "message": "You exceeded your current quota"}}


@pytest.mark.asyncio
async def test_openai_fails_mock_produces_and_is_attributed(async_db, wire):
    await _set_order(async_db, "openai:gpt-image-1, mock:mock-image-1", "true")
    with respx.mock:
        respx.post(_IMG_URL).mock(return_value=httpx.Response(429, json=_QUOTA_ERR))
        r = client.post("/v1/images/generations", json={"prompt": "a small orange cat", "n": 1}, headers={"Authorization": "Bearer gw_test"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["provider"] == "mock", body
    assert body["model"] == "mock-image-1", body
    assert body["data"][0]["b64_json"]


@pytest.mark.asyncio
async def test_gpt_image_1_path_unchanged_when_primary_ok(async_db, wire):
    await _set_order(async_db, "openai:gpt-image-1, mock:mock-image-1", "true")
    with respx.mock:
        respx.post(_IMG_URL).mock(return_value=httpx.Response(200, json=_OK_IMAGE))
        r = client.post("/v1/images/generations", json={"prompt": "a small orange cat", "n": 1}, headers={"Authorization": "Bearer gw_test"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["provider"] == "openai", body
    assert body["model"] == "gpt-image-1", body
    assert body["data"][0]["b64_json"] == "aGVsbG8="


@pytest.mark.asyncio
async def test_mock_does_not_rescue_when_disabled(async_db, wire):
    # mock is in the order but the kill-switch is off — it must NOT be used.
    await _set_order(async_db, "openai:gpt-image-1, mock:mock-image-1", "false")
    with respx.mock:
        respx.post(_IMG_URL).mock(return_value=httpx.Response(429, json=_QUOTA_ERR))
        r = client.post("/v1/images/generations", json={"prompt": "a small orange cat", "n": 1}, headers={"Authorization": "Bearer gw_test"})
    # openai failed and mock was not allowed to rescue → a real error, no mock image.
    assert r.status_code >= 400, r.text
    if r.headers.get("content-type", "").startswith("application/json"):
        body = r.json()
        assert body.get("provider") != "mock"
