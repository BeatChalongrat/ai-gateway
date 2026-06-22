"""ElevenLabs TTS adapter unit tests (Sprint 4) — sync path, NO live calls."""

from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
import respx

import app.services.media_gen.adapters.elevenlabs_tts as el
from app.core.exceptions import ProviderKeyMissing
from app.services.media_gen.adapters.elevenlabs_tts import ElevenLabsTTSAdapter
from app.services.media_gen.adapters.replicate_base import ProviderAuthError

_VOICE = "21m00Tcm4TlvDq8ikWAM"
_TTS = f"https://api.elevenlabs.io/v1/text-to-speech/{_VOICE}"


@pytest.fixture
def budget(monkeypatch):
    calls = {"reserve": 0, "release": 0}

    async def _reserve(estimate, **kw):
        calls["reserve"] += 1
        return True

    async def _release(estimate, **kw):
        calls["release"] += 1

    monkeypatch.setattr(el, "check_and_reserve_session_budget", _reserve)
    monkeypatch.setattr(el, "release_session_budget", _release)
    return calls


def _meta(**kw):
    m = {"estimate_usd": "0.0011", "text": "hello there", "voice_id": _VOICE}
    m.update(kw)
    return m


async def _key():
    return "xi-FAKE_elevenlabs_key"


@pytest.mark.asyncio
@respx.mock
async def test_submit_sync_done(budget):
    respx.post(_TTS).mock(return_value=httpx.Response(200, content=b"mp3bytes"))
    a = ElevenLabsTTSAdapter(key_resolver=_key)
    res = await a.submit_job("hello there", _meta())
    assert res.external_job_id is None
    assert res.initial_status == "done"
    assert res.sync_result.result_metadata["content_type"] == "audio/mpeg"
    data = await a.fetch_result_bytes(res.sync_result.result_url)
    assert data == b"mp3bytes"
    assert budget["reserve"] == 1 and budget["release"] == 0


@pytest.mark.asyncio
@respx.mock
async def test_no_key_raises_and_refunds(budget):
    async def _none():
        return None

    route = respx.post(_TTS).mock(return_value=httpx.Response(200, content=b"x"))
    a = ElevenLabsTTSAdapter(key_resolver=_none)
    with pytest.raises(ProviderKeyMissing):
        await a.submit_job("hi", _meta())
    assert route.call_count == 0
    assert budget["reserve"] == 1 and budget["release"] == 1


@pytest.mark.asyncio
@respx.mock
async def test_401_raises_and_refunds(budget):
    respx.post(_TTS).mock(return_value=httpx.Response(401, json={"detail": "no"}))
    a = ElevenLabsTTSAdapter(key_resolver=_key)
    with pytest.raises(ProviderAuthError):
        await a.submit_job("hi", _meta())
    assert budget["release"] == 1


def test_estimate_cost():
    a = ElevenLabsTTSAdapter()
    mc = SimpleNamespace(cost_per_character=Decimal("0.0001"))
    # 11 chars * 0.0001 = 0.0011
    assert a.estimate_cost_usd({"text": "hello there"}, mc) == Decimal("0.0011")


@pytest.mark.asyncio
async def test_poll_not_supported():
    a = ElevenLabsTTSAdapter(key_resolver=_key)
    with pytest.raises(NotImplementedError):
        await a.poll_job("nope")


@pytest.mark.asyncio
@respx.mock
async def test_token_never_leaks(budget):
    secret = "xi-ELEVENLABS_SECRET_must_not_leak"

    async def _skey():
        return secret

    a = ElevenLabsTTSAdapter(key_resolver=_skey)
    assert secret not in repr(a)
    assert secret not in str(a.__dict__)
    respx.post(_TTS).mock(return_value=httpx.Response(401, json={"detail": "no"}))
    with pytest.raises(ProviderAuthError) as exc_info:
        await a.submit_job("hi", _meta())
    assert secret not in str(exc_info.value)
