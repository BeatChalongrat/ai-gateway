"""ModerationService tests — all HTTP calls are mocked with respx."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.services.moderation_service import (
    MODERATION_ENDPOINT,
    MAX_INPUT_CHARS,
    ModerationService,
)


@pytest.fixture
def svc() -> ModerationService:
    return ModerationService(timeout=1.0)


def _mod_response(flagged: bool, categories: dict, scores: dict) -> dict:
    return {
        "id": "modr-test",
        "model": "text-moderation-latest",
        "results": [
            {
                "flagged": flagged,
                "categories": categories,
                "category_scores": scores,
            }
        ],
    }


@pytest.mark.asyncio
class TestModerationAllowed:
    @respx.mock
    async def test_clean_text_not_flagged(self, svc):
        respx.post(MODERATION_ENDPOINT).mock(
            return_value=httpx.Response(
                200,
                json=_mod_response(
                    flagged=False,
                    categories={"violence": False, "hate": False},
                    scores={"violence": 0.01, "hate": 0.02},
                ),
            )
        )
        result = await svc.check("hello world", "sk-test")
        assert result.flagged is False
        assert result.categories == {}
        assert result.error is None

    @respx.mock
    async def test_scores_above_threshold_reported(self, svc):
        respx.post(MODERATION_ENDPOINT).mock(
            return_value=httpx.Response(
                200,
                json=_mod_response(
                    flagged=False,
                    categories={},
                    scores={"hate": 0.15, "violence": 0.05, "sexual": 0.2},
                ),
            )
        )
        result = await svc.check("x", "sk-test")
        assert result.scores == {"hate": 0.15, "sexual": 0.2}
        assert "violence" not in result.scores  # below 0.1 threshold


@pytest.mark.asyncio
class TestModerationBlocked:
    @respx.mock
    async def test_violent_content_flagged(self, svc):
        respx.post(MODERATION_ENDPOINT).mock(
            return_value=httpx.Response(
                200,
                json=_mod_response(
                    flagged=True,
                    categories={"violence": True, "harassment": False},
                    scores={"violence": 0.95, "harassment": 0.03},
                ),
            )
        )
        result = await svc.check("very bad content", "sk-test")
        assert result.flagged is True
        assert result.categories == {"violence": True}
        assert result.scores == {"violence": 0.95}


@pytest.mark.asyncio
class TestFailOpen:
    @respx.mock
    async def test_timeout_fails_open(self, svc):
        respx.post(MODERATION_ENDPOINT).mock(
            side_effect=httpx.TimeoutException("upstream slow")
        )
        result = await svc.check("x", "sk-test")
        assert result.flagged is False
        assert "timeout" in result.error

    @respx.mock
    async def test_401_fails_open(self, svc):
        respx.post(MODERATION_ENDPOINT).mock(
            return_value=httpx.Response(401, json={"error": {"message": "bad key"}})
        )
        result = await svc.check("x", "sk-invalid")
        assert result.flagged is False
        assert result.error == "http_401"

    @respx.mock
    async def test_500_fails_open(self, svc):
        respx.post(MODERATION_ENDPOINT).mock(
            return_value=httpx.Response(500, text="internal error")
        )
        result = await svc.check("x", "sk-test")
        assert result.flagged is False
        assert result.error == "http_500"

    @respx.mock
    async def test_malformed_response_fails_open(self, svc):
        respx.post(MODERATION_ENDPOINT).mock(
            return_value=httpx.Response(200, json={"unexpected": "shape"})
        )
        result = await svc.check("x", "sk-test")
        assert result.flagged is False
        assert result.error.startswith("parse_error")

    async def test_empty_key_fails_open(self, svc):
        result = await svc.check("x", "")
        assert result.flagged is False
        assert result.error == "no_api_key"

    async def test_empty_text_not_flagged(self, svc):
        result = await svc.check("", "sk-test")
        assert result.flagged is False
        assert result.error is None


@pytest.mark.asyncio
class TestInputCapping:
    @respx.mock
    async def test_text_capped_to_max_chars(self, svc):
        import json as _json

        long_text = "a" * (MAX_INPUT_CHARS + 500)
        route = respx.post(MODERATION_ENDPOINT).mock(
            return_value=httpx.Response(
                200,
                json=_mod_response(flagged=False, categories={}, scores={}),
            )
        )
        await svc.check(long_text, "sk-test")
        assert route.called
        sent = _json.loads(route.calls.last.request.content.decode())
        assert sent["input"] == "a" * MAX_INPUT_CHARS
        assert len(sent["input"]) == MAX_INPUT_CHARS
