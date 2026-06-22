"""ReplicateMusicAdapter unit tests (Sprint 4) — NO live calls.

Mirrors test_replicate_adapter.py: respx mocks httpx, the spend guards +
resolver are monkeypatched on replicate_base (where the shared logic lives).
"""

from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
import respx

from app.core.config import settings
import app.services.media_gen.adapters.replicate_base as rb
from app.core.exceptions import ProviderKeyMissing
from app.services.media_gen.adapters.replicate_base import ProviderAuthError
from app.services.media_gen.adapters.replicate_music import ReplicateMusicAdapter
from app.services.media_gen.spend_guard import SpendCapExceeded

_PRED = "https://api.replicate.com/v1/models/meta/musicgen/predictions"
_FAKE_TOKEN = "r8_FAKE_music_token_do_not_log_456"


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setattr(settings, "replicate_webhook_base_url", "")
    monkeypatch.setattr(settings, "replicate_webhook_secret", None)
    monkeypatch.setattr(settings, "replicate_request_timeout_sec", 600)

    async def _fake_resolver():
        return _FAKE_TOKEN

    monkeypatch.setattr(rb, "resolve_replicate_key", _fake_resolver)


@pytest.fixture
def budget(monkeypatch):
    calls = {"reserve": 0, "release": 0}

    async def _reserve(estimate, **kw):
        calls["reserve"] += 1
        return True

    async def _release(estimate, **kw):
        calls["release"] += 1

    monkeypatch.setattr(rb, "check_and_reserve_session_budget", _reserve)
    monkeypatch.setattr(rb, "release_session_budget", _release)
    return calls


def _meta(**kw):
    m = {"estimate_usd": "0.0016", "replicate_model": "meta/musicgen", "duration_sec": 8}
    m.update(kw)
    return m


@pytest.mark.asyncio
@respx.mock
async def test_submit_happy_path(budget):
    route = respx.post(_PRED).mock(
        return_value=httpx.Response(201, json={"id": "pred_music", "status": "starting"})
    )
    res = await ReplicateMusicAdapter().submit_job("lofi beats", _meta())
    assert res.external_job_id == "pred_music"
    assert route.call_count == 1
    assert budget["reserve"] == 1 and budget["release"] == 0


@pytest.mark.asyncio
@respx.mock
async def test_submit_no_key_raises_no_http(budget):
    async def _none():
        return None

    route = respx.post(_PRED).mock(return_value=httpx.Response(201, json={"id": "x"}))
    a = ReplicateMusicAdapter(key_resolver=_none)
    with pytest.raises(ProviderKeyMissing):
        await a.submit_job("lofi", _meta())
    assert route.call_count == 0
    assert budget["reserve"] == 1 and budget["release"] == 1


@pytest.mark.asyncio
@respx.mock
async def test_submit_401_raises_and_refunds(budget):
    respx.post(_PRED).mock(return_value=httpx.Response(401, json={"detail": "no"}))
    with pytest.raises(ProviderAuthError):
        await ReplicateMusicAdapter().submit_job("lofi", _meta())
    assert budget["release"] == 1


@pytest.mark.asyncio
@respx.mock
async def test_poll_succeeded_returns_audio_url():
    respx.get("https://api.replicate.com/v1/predictions/pred_music").mock(
        return_value=httpx.Response(
            200,
            json={"status": "succeeded", "output": "https://replicate.delivery/song.mp3"},
        )
    )
    res = await ReplicateMusicAdapter().poll_job("pred_music")
    assert res.status == "done"
    assert res.result_url == "https://replicate.delivery/song.mp3"


@pytest.mark.asyncio
@respx.mock
async def test_poll_failed():
    respx.get("https://api.replicate.com/v1/predictions/pred_x").mock(
        return_value=httpx.Response(200, json={"status": "failed", "error": "boom"})
    )
    res = await ReplicateMusicAdapter().poll_job("pred_x")
    assert res.status == "failed"


def test_estimate_cost():
    a = ReplicateMusicAdapter()
    mc = SimpleNamespace(cost_per_second=Decimal("0.0002"))
    assert a.estimate_cost_usd({"duration_sec": 8}, mc) == Decimal("0.0016")
    # Clamp to 30s.
    assert a.estimate_cost_usd({"duration_sec": 120}, mc) == Decimal("0.0060")


@pytest.mark.asyncio
@respx.mock
async def test_token_never_leaks(budget):
    secret = "r8_MUSIC_SECRET_must_not_leak"

    async def _key():
        return secret

    a = ReplicateMusicAdapter(key_resolver=_key)
    assert secret not in repr(a)
    assert secret not in str(a.__dict__)
    respx.post(_PRED).mock(return_value=httpx.Response(401, json={"detail": "no"}))
    with pytest.raises(ProviderAuthError) as exc_info:
        await a.submit_job("lofi", _meta())
    assert secret not in str(exc_info.value)
