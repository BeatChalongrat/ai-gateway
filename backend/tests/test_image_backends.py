"""FIX67 — image backend registry, mock backend, and the fallback walker."""

import base64

import pytest

from app.core.exceptions import ProviderUnavailableError
from app.services.media_gen.destinations import Destination
from app.services.media_gen.image_backends import (
    registered_image_providers,
)
from app.services.media_gen.image_backends.base import (
    ImageBackend,
    ImageGenResult,
    register_image_backend,
)
from app.services.media_gen.image_backends.dispatch import generate_with_fallback
from app.services.media_gen.image_backends.mock_image import MockImageBackend


def test_real_backends_registered():
    assert {"openai", "mock"} <= registered_image_providers()


@pytest.mark.asyncio
async def test_mock_backend_returns_a_valid_png():
    r = await MockImageBackend().generate(None, model="mock-image-1", prompt="cat")
    assert r.provider == "mock"
    assert r.cost_usd == 0.0
    png = base64.b64decode(r.data[0]["b64_json"])
    assert png[:8] == b"\x89PNG\r\n\x1a\n"  # real PNG signature


class _Raises(ImageBackend):
    provider_name = "fix67_raises"

    async def generate(self, session, **kw):
        raise ProviderUnavailableError("primary out of credits")


class _Ok(ImageBackend):
    provider_name = "fix67_ok"

    async def generate(self, session, *, model, **kw):
        return ImageGenResult(provider="fix67_ok", model=model, data=[{"b64_json": "eA=="}], cost_usd=0.0)


@pytest.fixture(autouse=True)
def _register_fakes():
    register_image_backend(_Raises())
    register_image_backend(_Ok())


@pytest.mark.asyncio
async def test_primary_fails_second_tier_produces():
    attempts = []
    order = [Destination("fix67_raises", "m1"), Destination("mock", "mock-image-1")]
    r = await generate_with_fallback(
        None, order, prompt="cat",
        on_attempt=lambda d, ok, e: attempts.append((d.provider, ok)),
    )
    assert r.provider == "mock"  # fell to the second tier
    assert attempts == [("fix67_raises", False), ("mock", True)]


@pytest.mark.asyncio
async def test_primary_ok_no_fallback():
    attempts = []
    order = [Destination("fix67_ok", "gpt-x"), Destination("mock", "mock-image-1")]
    r = await generate_with_fallback(
        None, order, prompt="cat",
        on_attempt=lambda d, ok, e: attempts.append((d.provider, ok)),
    )
    assert r.provider == "fix67_ok"
    assert attempts == [("fix67_ok", True)]  # mock never tried


@pytest.mark.asyncio
async def test_all_fail_raises_last_error():
    order = [Destination("fix67_raises", "m1")]
    with pytest.raises(ProviderUnavailableError):
        await generate_with_fallback(None, order, prompt="cat")


@pytest.mark.asyncio
async def test_empty_order_raises():
    with pytest.raises(ProviderUnavailableError):
        await generate_with_fallback(None, [], prompt="cat")
