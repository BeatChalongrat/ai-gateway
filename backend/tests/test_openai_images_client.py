import json

import httpx
import pytest
import respx

from app.services.providers.openai_images_client import generate_image, image_cost_usd

_KEY_PATH = "app.services.provider_key_service.ProviderKeyService.get_active_key_async"


def test_cost_dalle3():
    assert image_cost_usd("dall-e-3", "1024x1024", "standard", 1) == 0.040
    assert image_cost_usd("dall-e-3", "1024x1024", "hd", 1) == 0.080
    assert image_cost_usd("dall-e-3", "1024x1792", "standard", 1) == 0.080
    assert image_cost_usd("dall-e-3", "1792x1024", "hd", 1) == 0.120
    # unknown size/quality -> base rate (don't under-bill), not 0
    assert image_cost_usd("dall-e-3", "999x999", "weird", 1) == 0.040


def test_cost_dalle2():
    assert image_cost_usd("dall-e-2", "1024x1024", "standard", 1) == 0.020
    assert image_cost_usd("dall-e-2", "512x512", "standard", 1) == 0.018
    assert image_cost_usd("dall-e-2", "256x256", "standard", 2) == round(0.016 * 2, 6)


@pytest.mark.asyncio
async def test_generate_image_request_and_parse(monkeypatch):
    async def _fake_key(self, session, provider):
        return "sk-test-key"

    monkeypatch.setattr(_KEY_PATH, _fake_key)

    with respx.mock:
        route = respx.post("https://api.openai.com/v1/images/generations").mock(
            return_value=httpx.Response(
                200,
                json={
                    "created": 1699000000,
                    "data": [
                        {"url": "https://img/x.png", "revised_prompt": "a revised cat"}
                    ],
                },
            )
        )
        result = await generate_image(
            None,  # session unused (key service is mocked)
            model="dall-e-3",
            prompt="a cat",
            size="1024x1024",
            quality="hd",
            style="natural",
        )

    sent = json.loads(route.calls[0].request.read())
    assert sent["model"] == "dall-e-3"
    assert sent["prompt"] == "a cat"
    assert sent["n"] == 1  # dall-e-3 is forced to n=1
    assert sent["quality"] == "hd"
    assert "style" not in sent  # OpenAI's images API rejects style for dall-e-3
    assert "response_format" not in sent  # ...and response_format
    assert route.calls[0].request.headers.get("authorization") == "Bearer sk-test-key"

    assert result.data[0]["url"] == "https://img/x.png"
    assert result.model == "dall-e-3"
    assert result.cost_usd == 0.080  # 1024x1024 hd


@pytest.mark.asyncio
async def test_generate_image_dalle2_n_and_no_quality(monkeypatch):
    async def _fake_key(self, session, provider):
        return "sk-test-key"

    monkeypatch.setattr(_KEY_PATH, _fake_key)

    with respx.mock:
        route = respx.post("https://api.openai.com/v1/images/generations").mock(
            return_value=httpx.Response(200, json={"created": 1, "data": [{"url": "u"}]})
        )
        result = await generate_image(
            None, model="dall-e-2", prompt="x", n=3, size="512x512"
        )

    sent = json.loads(route.calls[0].request.read())
    assert sent["n"] == 3  # dall-e-2 honours n
    assert "quality" not in sent  # dall-e-2: no quality/style fields
    assert "style" not in sent
    assert result.cost_usd == round(0.018 * 3, 6)


def test_cost_gpt_image():
    assert image_cost_usd("gpt-image-1", "1024x1024", "low", 1) == 0.011
    assert image_cost_usd("gpt-image-1", "1024x1024", "medium", 1) == 0.042
    assert image_cost_usd("gpt-image-1", "1024x1024", "high", 1) == 0.167
    # standard/hd are mapped into gpt-image qualities
    assert image_cost_usd("gpt-image-1", "1024x1024", "standard", 1) == 0.042  # -> medium
    assert image_cost_usd("gpt-image-1", "1024x1024", "hd", 1) == 0.167  # -> high
    # auto / unknown -> medium estimate (don't under-bill)
    assert image_cost_usd("gpt-image-1", "1024x1024", "auto", 1) == 0.042


@pytest.mark.asyncio
async def test_generate_gpt_image_b64_and_quality(monkeypatch):
    async def _fake_key(self, session, provider):
        return "sk-test-key"

    monkeypatch.setattr(_KEY_PATH, _fake_key)

    with respx.mock:
        route = respx.post("https://api.openai.com/v1/images/generations").mock(
            return_value=httpx.Response(
                200,
                json={
                    "created": 1,
                    "data": [{"b64_json": "AAAA"}],  # gpt-image returns b64, no url
                    "usage": {"total_tokens": 1500},
                },
            )
        )
        result = await generate_image(
            None, model="gpt-image-1", prompt="a cat", size="1024x1024", quality="high"
        )

    sent = json.loads(route.calls[0].request.read())
    assert sent["model"] == "gpt-image-1"
    assert sent["quality"] == "high"  # gpt-image quality enum
    assert "style" not in sent
    assert "response_format" not in sent
    assert result.data[0]["b64_json"] == "AAAA"
    assert result.data[0].get("url") is None
    assert result.cost_usd == 0.167  # high 1024x1024
