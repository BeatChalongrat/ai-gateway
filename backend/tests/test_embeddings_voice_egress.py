"""Route-level proof for FIX 2 — the embeddings + voice gateway routes run
the DLP raw-egress scan BEFORE the provider call.

We call the route functions directly (bypassing FastAPI DI) with a real
``async_db`` session, a stub ``auth``, and a SPY substituted for the OpenAI
utility client. The spy records whether the provider was called — proving:

* a secret-bearing input is BLOCKED (403) and the provider is NEVER called
  (zero-request-on-block);
* identity PII passes RAW (by design) and the provider IS called with the
  unmasked text;
* a ``block``-policy tenant fails closed on identity PII too.

No live OpenAI key is needed — the spy stands in for the outbound HTTP call.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.api.routes import public_embeddings, public_voice
from app.core.exceptions import RawEgressBlockedError
from app.models.tenant import TenantORM

PII_NAME = "สมชาย ใจดี"
PII_EMAIL = "alice@example.com"  # regex-detectable identity (no NER dependency)
SECRET_API_KEY = "sk-ant-api03-aBcDeFgHiJkLmNoPqRsTuVwXyZ012345"

_AUTH = SimpleNamespace(
    tenant_id="t-fix2", project_id="", portal_user_id="u-fix2"
)


async def _seed_tenant(async_db, **policy) -> None:
    """Insert a tenant row so ``session.get(TenantORM, ...)`` in the route
    returns a real tenant carrying the requested egress policy."""
    tenant = TenantORM(
        tenant_id="t-fix2",
        name="FIX2 Tenant",
        slug="fix2-tenant",
        **policy,
    )
    async_db.add(tenant)
    # flush, NOT commit — the shared integration DB persists commits across
    # tests; flush makes the row visible to session.get within this session
    # while the fixture's rollback cleans it up afterwards.
    await async_db.flush()


# ---------------------------------------------------------------------------
# Embeddings route
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_embeddings_secret_blocks_before_provider_call(async_db, monkeypatch):
    calls: list[str] = []

    async def spy(text, *, model=None):
        calls.append(text)
        return [0.0, 0.0]

    monkeypatch.setattr(public_embeddings, "generate_embedding", spy)

    payload = public_embeddings.EmbeddingRequest(input=f"api key {SECRET_API_KEY}")
    with pytest.raises(RawEgressBlockedError):
        await public_embeddings.create_embedding(payload, _AUTH, async_db)

    assert calls == []  # zero-request-on-block: the secret never egressed


@pytest.mark.asyncio
async def test_embeddings_identity_passes_raw_to_provider(async_db, monkeypatch):
    calls: list[str] = []

    async def spy(text, *, model=None):
        calls.append(text)
        return [0.1, 0.2, 0.3]

    monkeypatch.setattr(public_embeddings, "generate_embedding", spy)

    text = f"notes about {PII_NAME}"
    payload = public_embeddings.EmbeddingRequest(input=text)
    resp = await public_embeddings.create_embedding(payload, _AUTH, async_db)

    # Identity PII reaches the provider RAW by design (a masked embedding is
    # useless) — the default tenant policy allows it (audited).
    assert calls == [text]
    assert resp["data"][0]["embedding"] == [0.1, 0.2, 0.3]


@pytest.mark.asyncio
async def test_embeddings_block_policy_fails_closed_on_identity(async_db, monkeypatch):
    await _seed_tenant(async_db, kb_pii_egress="block")

    calls: list[str] = []

    async def spy(text, *, model=None):
        calls.append(text)
        return [0.0]

    monkeypatch.setattr(public_embeddings, "generate_embedding", spy)

    payload = public_embeddings.EmbeddingRequest(input=f"email {PII_EMAIL}")
    with pytest.raises(RawEgressBlockedError):
        await public_embeddings.create_embedding(payload, _AUTH, async_db)

    assert calls == []  # strict tenant → identity PII blocked, no egress


# ---------------------------------------------------------------------------
# Voice TTS route (synthesize)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_voice_tts_secret_blocks_before_provider_call(async_db, monkeypatch):
    calls: list[str] = []

    async def spy(text, *, voice="alloy", speed=1.0, model=None):
        calls.append(text)
        return b"mp3"

    monkeypatch.setattr(public_voice, "synthesize", spy)

    payload = public_voice.SynthesizeRequest(
        text=f"read my key {SECRET_API_KEY}", voice="alloy", speed=1.0
    )
    with pytest.raises(RawEgressBlockedError):
        await public_voice.voice_synthesize(payload, _AUTH, async_db)

    assert calls == []  # secret never reaches TTS


@pytest.mark.asyncio
async def test_voice_tts_identity_passes_raw(async_db, monkeypatch):
    calls: list[str] = []

    async def spy(text, *, voice="alloy", speed=1.0, model=None):
        calls.append(text)
        return b"ID3-mp3-bytes"

    monkeypatch.setattr(public_voice, "synthesize", spy)

    text = f"say hello to {PII_NAME}"
    payload = public_voice.SynthesizeRequest(text=text, voice="alloy", speed=1.0)
    resp = await public_voice.voice_synthesize(payload, _AUTH, async_db)

    assert calls == [text]
    assert resp.media_type == "audio/mpeg"
    assert resp.body == b"ID3-mp3-bytes"
