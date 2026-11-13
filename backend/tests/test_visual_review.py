import base64
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from PIL import Image
from app.schemas.public import ChatCompletionsRequest
from app.services.dlp.visual_review import enforce_visual_review_policy


def page():
    buf = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def request(images=None, **kw):
    return ChatCompletionsRequest(model="mock-vision", visual_review=True, messages=[{
        "role": "user", "content": [{"type": "image_url", "image_url": {"url": image}} for image in (images if images is not None else [page()])]
    }], **kw)


@pytest.mark.asyncio
@pytest.mark.parametrize("tenant", [None, SimpleNamespace(media_pii_egress="block"), SimpleNamespace()])
async def test_raw_page_egress_requires_explicit_tenant_allow(tenant):
    with pytest.raises(HTTPException) as error:
        await enforce_visual_review_policy(request(), SimpleNamespace(tenant_id="tenant"), SimpleNamespace(get=AsyncMock(return_value=tenant)))
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_all_eight_inline_pages_are_validated_without_network():
    await enforce_visual_review_policy(request([page()] * 8), SimpleNamespace(tenant_id="tenant"), SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(media_pii_egress="allow"))))


@pytest.mark.asyncio
@pytest.mark.parametrize("images", [[], [page()] * 9, ["https://example.com/page.png"], ["data:image/png;base64,YWJj"]])
async def test_external_invalid_or_incomplete_page_inputs_are_rejected(images):
    with pytest.raises(HTTPException) as error:
        await enforce_visual_review_policy(request(images), SimpleNamespace(tenant_id="tenant"), SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(media_pii_egress="allow"))))
    assert error.value.status_code == 422


@pytest.mark.asyncio
async def test_review_is_nonstreaming():
    with pytest.raises(HTTPException) as error:
        await enforce_visual_review_policy(request(stream=True), SimpleNamespace(tenant_id="tenant"), SimpleNamespace(get=AsyncMock()))
    assert error.value.status_code == 422


@pytest.mark.asyncio
async def test_chat_route_enforces_raw_page_policy_even_when_dlp_is_disabled(monkeypatch):
    from app.api.routes.public_chat import chat_completions
    from app.core.config import settings
    monkeypatch.setattr(settings, "dlp_enabled", False)
    with pytest.raises(HTTPException) as error:
        await chat_completions(request(), SimpleNamespace(), SimpleNamespace(), SimpleNamespace(tenant_id="tenant"), SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(media_pii_egress="block"))))
    assert error.value.status_code == 403
