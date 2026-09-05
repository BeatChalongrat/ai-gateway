"""Image gen DLP profile migration to MEDIA_GEN (Sprint 2A Batch E).

Confirms the swapped profile on the real /v1/images/generations route:
secret BLOCKs, identity PII PASSES through, plain prompts are unaffected.
The OpenAI image client is monkeypatched — NO real provider calls.
"""

import pytest
from starlette.testclient import TestClient

from app.core.config import settings
from app.main import app as main_app


class _FakeResult:
    created = 1_700_000_000
    model = "gpt-image-1"
    cost_usd = 0.04
    data = [
        {"url": "https://example.com/x.png", "b64_json": None, "revised_prompt": "art"}
    ]


async def _fake_generate_image(session, **kwargs):
    return _FakeResult()


def _post(prompt: str):
    return TestClient(main_app).post(
        "/v1/images/generations",
        json={"prompt": prompt, "model": "gpt-image-1"},
        headers={"Authorization": "Bearer test"},
    )


@pytest.fixture(autouse=True)
def _mock_image_client(monkeypatch):
    monkeypatch.setattr(settings, "dlp_enabled", True)
    monkeypatch.setattr(
        "app.services.providers.openai_images_client.generate_image", _fake_generate_image
    )


def test_secret_prompt_blocked():
    r = _post("a poster with my key sk-proj-abc123def456ghi789jkl012mno345pqr678")
    assert r.status_code == 403, r.text


def test_pii_identity_prompt_passes():
    r = _post("draw me, my email is john@example.com")
    assert r.status_code == 200, r.text


def test_plain_prompt_ok():
    r = _post("a red bicycle on a sunny beach")
    assert r.status_code == 200, r.text
