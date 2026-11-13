import base64
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from PIL import Image
from fastapi import HTTPException
from pydantic import ValidationError

from app.services.media_gen.image_inputs import validate_image_inputs
from app.services.media_gen.image_backends.dispatch import generate_with_fallback
from app.services.media_gen.destinations import Destination
from app.services.providers.openai_images_client import generate_image, usage_cost_usd
from app.schemas.media_gen_public import VideoGenPublicRequest
from app.schemas.media_gen import VideoGenSubmitRequest
from app.services.media_gen.submit_helpers import estimate_and_validate_cost, validate_model_capability


def reference():
    out = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(out, format="PNG")
    return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode()


def test_reference_image_validation():
    checked = validate_image_inputs([reference()])
    assert checked[0][2] == "image/png"
    assert checked[0][1].startswith(b"\x89PNG")


@pytest.mark.parametrize("values", [[], ["https://example.com/a.png"], ["file:///etc/passwd"],
    ["data:image/png;base64,!!"], ["data:image/png;base64,YWJj"], [reference()]*5,
    [reference().replace("image/png", "image/jpeg")], ["data:image/png;base64,"+"A"*5_600_000]])
def test_reference_rejects_unsafe_or_invalid_inputs(values):
    with pytest.raises(ValueError):
        validate_image_inputs(values)


@pytest.mark.asyncio
async def test_edit_uses_multipart_and_counts_input_tokens(monkeypatch):
    monkeypatch.setattr("app.services.provider_key_service.ProviderKeyService.get_active_key_async", AsyncMock(return_value="test-key"))
    with respx.mock:
        route = respx.post("https://api.openai.com/v1/images/edits").mock(return_value=httpx.Response(200, json={
            "data": [{"b64_json": "output"}],
            "usage": {"input_tokens_details": {"text_tokens": 100, "image_tokens": 200}, "output_tokens": 300},
        }))
        result = await generate_image(None, model="gpt-image-2.6-flare", prompt="Keep รายงาน exactly", source_images=[reference()])
    request = route.calls[0].request
    assert "multipart/form-data" in request.headers["content-type"]
    assert b'name="image[]"' in request.content
    assert "รายงาน".encode() in request.content
    assert result.cost_usd == pytest.approx(.0111)


def test_missing_usage_is_explicit_estimate_fallback():
    assert usage_cost_usd("gpt-image-2.6-flare", {}, .042) == .042
    assert usage_cost_usd("unknown", {}, .042) == .042


def test_advertised_video_duration_always_fits_public_schema(monkeypatch):
    from app.core.config import settings
    from app.api.routes.public_videos import _max_video_seconds
    monkeypatch.setattr(settings, "replicate_max_video_duration_sec", 120)
    assert _max_video_seconds() == 60
    VideoGenPublicRequest(prompt="demo", model="video", duration_sec=_max_video_seconds())


@pytest.mark.asyncio
async def test_edit_never_falls_back_to_text_only_mock():
    from app.core.exceptions import ProviderUnavailableError
    with pytest.raises(ProviderUnavailableError, match="editing"):
        await generate_with_fallback(None, [Destination("mock", "mock-image-1")], prompt="edit", source_images=[reference()])


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["block", "unknown", None])
async def test_raw_reference_egress_fails_closed_before_provider(monkeypatch, policy):
    from app.api.routes.public_images import generate_images
    from app.schemas.public import ImageGenerationRequest
    from app.core.exceptions import DLPViolationError
    from app.core.config import settings
    monkeypatch.setattr(settings, "dlp_enabled", False)
    session = AsyncMock()
    session.get.return_value = SimpleNamespace(media_pii_egress=policy) if policy is not None else None
    with pytest.raises(DLPViolationError, match="reference-image"):
        await generate_images(ImageGenerationRequest(prompt="edit", source_images=[reference()]), SimpleNamespace(tenant_id="t"), session)


@pytest.mark.parametrize("schema", [VideoGenPublicRequest, VideoGenSubmitRequest])
@pytest.mark.parametrize("options", [{"resolution": "1080p"}, {"has_audio": True}])
def test_video_does_not_accept_ignored_controls(schema, options):
    with pytest.raises(ValidationError):
        schema(prompt="demo", model="video", **options)


@pytest.mark.asyncio
@pytest.mark.parametrize("duration", [0, -1, 1000])
async def test_video_never_silently_shortens_duration(duration):
    with pytest.raises(HTTPException) as exc:
        await estimate_and_validate_cost(None, "t", "u", "video", None, {"duration_sec": duration})
    assert exc.value.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,model,active,status", [("openai", "sora-2", True, 410), ("missing", "video", True, 400), ("replicate", "video", False, 400)])
async def test_retired_or_unimplemented_video_models_fail_before_spend(provider, model, active, status):
    mc = SimpleNamespace(provider=provider, model=model, is_active=active, supports_video_gen=True)
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: mc))
    with pytest.raises(HTTPException) as exc:
        await validate_model_capability(db, model, "supports_video_gen")
    assert exc.value.status_code == status
