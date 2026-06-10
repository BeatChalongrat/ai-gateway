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
