"""FIX125 M3 — /v1/models offers reasoning "off" only where it is true.

Before FIX125 every reasoning row listed ``modes`` with "off". For most of
them the adapter can only stop ASKING for more thinking: DeepSeek / Groq
(``openai_compatible_reasoning``), the OpenAI Responses rows and parse-only
self-hosted boxes think anyway, so the client offered a switch that did
nothing. "off" is real for the Qwen/vLLM ``enable_thinking`` strategy (the
chat-template off-switch, FIX72) and for Anthropic (no ``thinking`` block);
Gemini is left as it was.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.domain.routing import ModelCandidate
from app.main import app

client = TestClient(app)

_ROWS = [
    ModelCandidate(
        provider="sranai", model="fix125-qwen-thinking",
        supports_reasoning=True, reasoning_strategy="openai_compatible_enable_thinking",
    ),
    ModelCandidate(
        provider="deepseek", model="fix125-deepseek-reasoner",
        supports_reasoning=True, reasoning_strategy="openai_compatible_reasoning",
    ),
    ModelCandidate(
        provider="sranai", model="fix125-parse-only-box",
        supports_reasoning=True, reasoning_strategy="openai_compatible_reasoning_content",
    ),
    ModelCandidate(
        provider="openai", model="fix125-o-series",
        supports_reasoning=True, reasoning_strategy="openai_responses_reasoning",
    ),
    ModelCandidate(
        provider="anthropic", model="fix125-claude-thinking",
        supports_reasoning=True, reasoning_strategy="none",
    ),
    ModelCandidate(
        provider="google", model="fix125-gemini-thinking",
        supports_reasoning=True, reasoning_strategy="gemini_thinking_level",
    ),
    ModelCandidate(provider="openai", model="fix125-plain"),
]


@pytest.fixture()
def by_id(monkeypatch):
    from app.api.routes import public_models as pm

    monkeypatch.setattr(pm, "get_all_candidates", lambda: list(_ROWS))
    monkeypatch.setattr(
        pm.ProviderKeyService,
        "providers_with_valid_keys",
        lambda self, session: {c.provider for c in _ROWS},
    )
    resp = client.get("/v1/models")
    assert resp.status_code == 200
    return {m["id"]: m for m in resp.json()["data"]}


def _modes(entry: dict) -> list[str]:
    return entry["reasoning"]["modes"]


def test_enable_thinking_row_offers_off(by_id):
    assert _modes(by_id["fix125-qwen-thinking"]) == ["off", "auto", "low", "medium", "high"]


def test_deepseek_reasoning_row_does_not_offer_off(by_id):
    modes = _modes(by_id["fix125-deepseek-reasoner"])
    assert "off" not in modes
    assert modes == ["auto", "low", "medium", "high"]


def test_parse_only_and_responses_rows_do_not_offer_off(by_id):
    assert "off" not in _modes(by_id["fix125-parse-only-box"])
    assert "off" not in _modes(by_id["fix125-o-series"])


def test_anthropic_and_gemini_keep_off(by_id):
    assert "off" in _modes(by_id["fix125-claude-thinking"])
    assert "off" in _modes(by_id["fix125-gemini-thinking"])


def test_non_reasoning_row_has_no_reasoning_block(by_id):
    assert by_id["fix125-plain"].get("reasoning") in (None, {})
