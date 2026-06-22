"""OpenAI TTS adapter unit tests (Sprint 4) — sync fast-path, NO live calls."""

from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
import respx

import app.services.media_gen.adapters.openai_tts as ot
from app.core.exceptions import ProviderKeyMissing
from app.services.media_gen.adapters.openai_tts import OpenAITTSAdapter
from app.services.media_gen.adapters.replicate_base import ProviderAuthError

_SPEECH = "https://api.openai.com/v1/audio/speech"


@pytest.fixture
def budget(monkeypatch):
    calls = {"reserve": 0, "release": 0}

    async def _reserve(estimate, **kw):
        calls["reserve"] += 1
        return True

    async def _release(estimate, **kw):
        calls["release"] += 1

    monkeypatch.setattr(ot, "check_and_reserve_session_budget", _reserve)
    monkeypatch.setattr(ot, "release_session_budget", _release)
    return calls


def _meta(**kw):
    m = {"estimate_usd": "0.0075", "text": "hello world", "voice": "alloy"}
    m.update(kw)
    return m


async def _key():
    return "sk-FAKE_openai_tts_key"


@pytest.mark.asyncio
@respx.mock
async def test_submit_sync_done(budget):
    respx.post(_SPEECH).mock(
        return_value=httpx.Response(200, content=b"ID3_fake_mp3_bytes")
    )
    a = OpenAITTSAdapter(key_resolver=_key)
    res = await a.submit_job("hello world", _meta())
    assert res.external_job_id is None
    assert res.initial_status == "done"
    assert res.sync_result is not None
    assert res.sync_result.result_metadata["content_type"] == "audio/mpeg"
    # The job loop fetches the stashed bytes back on the same instance.
    data = await a.fetch_result_bytes(res.sync_result.result_url)
    assert data == b"ID3_fake_mp3_bytes"
    assert budget["reserve"] == 1 and budget["release"] == 0


@pytest.mark.asyncio
@respx.mock
async def test_no_key_raises_and_refunds(budget):
    async def _none():
        return None

    route = respx.post(_SPEECH).mock(return_value=httpx.Response(200, content=b"x"))
    a = OpenAITTSAdapter(key_resolver=_none)
    with pytest.raises(ProviderKeyMissing):
        await a.submit_job("hi", _meta())
    assert route.call_count == 0
    assert budget["reserve"] == 1 and budget["release"] == 1


@pytest.mark.asyncio
@respx.mock
async def test_401_raises_and_refunds(budget):
    respx.post(_SPEECH).mock(return_value=httpx.Response(401, json={"error": "no"}))
    a = OpenAITTSAdapter(key_resolver=_key)
    with pytest.raises(ProviderAuthError):
        await a.submit_job("hi", _meta())
    assert budget["release"] == 1


def test_estimate_cost():
    a = OpenAITTSAdapter()
    mc = SimpleNamespace(cost_per_character=Decimal("0.000015"))
    # 500 chars * 0.000015 = 0.0075
    assert a.estimate_cost_usd({"text": "x" * 500}, mc) == Decimal("0.0075")


@pytest.mark.asyncio
async def test_poll_not_supported():
    a = OpenAITTSAdapter(key_resolver=_key)
    with pytest.raises(NotImplementedError):
        await a.poll_job("nope")


@pytest.mark.asyncio
@respx.mock
async def test_token_never_leaks(budget):
    secret = "sk-OPENAI_SECRET_must_not_leak"

    async def _skey():
        return secret

    a = OpenAITTSAdapter(key_resolver=_skey)
    assert secret not in repr(a)
    assert secret not in str(a.__dict__)
    respx.post(_SPEECH).mock(return_value=httpx.Response(401, json={"error": "no"}))
    with pytest.raises(ProviderAuthError) as exc_info:
        await a.submit_job("hi", _meta())
    assert secret not in str(exc_info.value)
