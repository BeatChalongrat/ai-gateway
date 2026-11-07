"""FIX11 §1 — the video capability answer (GET /v1/video/config).

Proves the capability is built from three ANDed signals (gate AND a configured
key AND an owner-verified model) and is demonstrated in BOTH states, without ever
sending a real generation request and without surfacing the key value.
"""

import pytest

from app.api.routes.public_videos import _resolve_video_capability
from app.core.config import settings


class _StubResult:
    def __init__(self, n: int) -> None:
        self._n = n

    def scalar(self) -> int:
        return self._n


class _StubSession:
    """Minimal async session: the only query the helper runs is the
    active-video-model count."""

    def __init__(self, model_count: int) -> None:
        self._n = model_count

    async def execute(self, *_a, **_k) -> _StubResult:  # noqa: ANN002, ANN003
        return _StubResult(self._n)


def _set(monkeypatch, *, gate: bool, key, verified: bool) -> None:
    monkeypatch.setattr(settings, "video_gen_enabled", gate)
    monkeypatch.setattr(settings, "replicate_api_token", key)
    monkeypatch.setattr(settings, "video_model_verified", verified)


@pytest.mark.asyncio
async def test_all_three_signals_enable(monkeypatch):
    _set(monkeypatch, gate=True, key="sekret-token-value", verified=True)
    cap = await _resolve_video_capability(_StubSession(1))
    assert cap["enabled"] is True
    assert cap["reason"] == ""
    assert cap["key_configured"] is True
    # The key value must NEVER appear in the capability payload.
    assert "sekret-token-value" not in str(cap)


@pytest.mark.asyncio
async def test_missing_key_disables_with_reason(monkeypatch):
    _set(monkeypatch, gate=True, key=None, verified=True)
    cap = await _resolve_video_capability(_StubSession(1))
    assert cap["enabled"] is False
    assert cap["key_configured"] is False
    assert "key" in cap["reason"].lower()


@pytest.mark.asyncio
async def test_unverified_model_disables_even_with_key(monkeypatch):
    # The trap: a key is present, but the model has not been verified current.
    _set(monkeypatch, gate=True, key="present", verified=False)
    cap = await _resolve_video_capability(_StubSession(1))
    assert cap["enabled"] is False
    assert cap["model_verified"] is False
    assert "verif" in cap["reason"].lower()


@pytest.mark.asyncio
async def test_gate_off_disables(monkeypatch):
    _set(monkeypatch, gate=False, key="present", verified=True)
    cap = await _resolve_video_capability(_StubSession(0))
    assert cap["enabled"] is False
    assert cap["reason"]


@pytest.mark.asyncio
async def test_no_active_video_model_disables(monkeypatch):
    _set(monkeypatch, gate=True, key="present", verified=True)
    cap = await _resolve_video_capability(_StubSession(0))
    assert cap["enabled"] is False
    assert "model" in cap["reason"].lower()
