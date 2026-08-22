"""Embeddings, voice and AI review follow the key the admin UI holds.

These three used to read ``settings.openai_api_key`` / ``anthropic_api_key``
directly, which made them a SECOND live source for a value the admin UI also
owns. The concrete failure that made this required rather than tidy: an admin
rotates the OpenAI key on the Providers page, chat follows it within the
30s cache TTL, and RAG search, Whisper, TTS and every AI-review scan go on
sending the old environment value — with nothing on screen saying so, and a
401 from the provider as the only symptom.

So the assertions here are not "a key is used" but "the key that dispatch
would use is used, before AND after a rotation". Each test rotates the key
through the ordinary admin flow (add the replacement, delete the old row) and
re-checks, because a client that reads the DB once at import would pass a
static check and still be broken.

The Authorization header is captured from a fake transport; no test here
performs real egress, and no assertion prints key material — they compare
against locally generated values.
"""

from __future__ import annotations

import pytest

from app.services.provider_key_service import ProviderKeyService, clear_keys_cache
from tests.conftest import _IntegrationSession

FIRST_KEY = "sk-proj-" + "1" * 60
ROTATED_KEY = "sk-proj-" + "2" * 60
ANTHROPIC_KEY = "sk-ant-api03-" + "3" * 60
STALE_ENV_KEY = "sk-proj-" + "9" * 60


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wipe(provider: str) -> None:
    """Remove every key row for ``provider`` from the integration DB."""
    from app.models.provider_key import ProviderKeyORM
    from app.models.system_setting import SystemSettingORM
    from app.services.provider_env_seed import KIND_KEY, marker_key

    session = _IntegrationSession()
    try:
        session.query(ProviderKeyORM).filter(
            ProviderKeyORM.provider_name == provider
        ).delete()
        marker = session.get(SystemSettingORM, marker_key(KIND_KEY, provider))
        if marker is not None:
            session.delete(marker)
        session.commit()
    finally:
        session.close()
    clear_keys_cache()


def _set_key(provider: str, key: str) -> None:
    """Make ``key`` the single usable key for ``provider``, as the UI would."""
    _wipe(provider)
    session = _IntegrationSession()
    try:
        ProviderKeyService().add_key(session, provider, key, label="admin")
        session.commit()
    finally:
        session.close()
    clear_keys_cache()


@pytest.fixture()
def stale_env(monkeypatch):
    """A DIFFERENT value in the environment, so a client that still reads it
    fails loudly instead of coincidentally matching the DB."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "openai_api_key", STALE_ENV_KEY, raising=False)
    monkeypatch.setattr(settings, "anthropic_api_key", STALE_ENV_KEY, raising=False)
    return STALE_ENV_KEY


class _CapturedAuth:
    """Records the Authorization header of the last outbound call."""

    def __init__(self) -> None:
        self.header: str | None = None


def _fake_client_factory(captured: _CapturedAuth, payload: dict | bytes):
    """A stand-in for ``httpx.AsyncClient`` that records and never egresses."""

    class _Resp:
        status_code = 200

        def json(self):
            return payload if isinstance(payload, dict) else {}

        @property
        def content(self):
            return payload if isinstance(payload, bytes) else b""

        @property
        def headers(self):
            return {"content-type": "audio/mpeg"}

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, **kw):
            captured.header = (headers or {}).get("Authorization")
            return _Resp()

    return _Client


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------


class TestEmbeddingsFollowTheResolver:
    @pytest.mark.asyncio
    async def test_uses_the_ui_key_and_follows_a_rotation(
        self, monkeypatch, stale_env
    ):
        from app.services.embeddings import openai_embeddings_client as mod

        captured = _CapturedAuth()
        monkeypatch.setattr(
            mod.httpx,
            "AsyncClient",
            _fake_client_factory(captured, {"data": [{"embedding": [0.1, 0.2]}]}),
        )

        _set_key("openai", FIRST_KEY)
        await mod.generate_embedding("hello")
        assert captured.header == f"Bearer {FIRST_KEY}"
        assert captured.header != f"Bearer {stale_env}"

        _set_key("openai", ROTATED_KEY)
        await mod.generate_embedding("hello again")
        assert captured.header == f"Bearer {ROTATED_KEY}"

    @pytest.mark.asyncio
    async def test_no_key_disables_the_endpoint(self, monkeypatch, stale_env):
        """The stale env value must NOT keep the endpoint alive."""
        from app.core.exceptions import ProviderUnavailableError
        from app.services.embeddings import openai_embeddings_client as mod

        _wipe("openai")
        assert await mod.embeddings_enabled() is False
        with pytest.raises(ProviderUnavailableError):
            await mod.generate_embedding("hello")


# ---------------------------------------------------------------------------
# Voice
# ---------------------------------------------------------------------------


class TestVoiceFollowsTheResolver:
    @pytest.mark.asyncio
    async def test_transcribe_uses_the_ui_key_and_follows_a_rotation(
        self, monkeypatch, stale_env
    ):
        from app.services.voice import openai_voice_client as mod

        captured = _CapturedAuth()
        monkeypatch.setattr(
            mod.httpx, "AsyncClient", _fake_client_factory(captured, {"text": "hi"})
        )

        _set_key("openai", FIRST_KEY)
        await mod.transcribe(
            b"audio-bytes", filename="a.webm", content_type="audio/webm"
        )
        assert captured.header == f"Bearer {FIRST_KEY}"

        _set_key("openai", ROTATED_KEY)
        await mod.transcribe(
            b"audio-bytes", filename="a.webm", content_type="audio/webm"
        )
        assert captured.header == f"Bearer {ROTATED_KEY}"

    @pytest.mark.asyncio
    async def test_synthesize_uses_the_ui_key_and_follows_a_rotation(
        self, monkeypatch, stale_env
    ):
        from app.services.voice import openai_voice_client as mod

        captured = _CapturedAuth()
        monkeypatch.setattr(
            mod.httpx, "AsyncClient", _fake_client_factory(captured, b"mp3-bytes")
        )

        _set_key("openai", FIRST_KEY)
        await mod.synthesize("hello")
        assert captured.header == f"Bearer {FIRST_KEY}"

        _set_key("openai", ROTATED_KEY)
        await mod.synthesize("hello")
        assert captured.header == f"Bearer {ROTATED_KEY}"

    @pytest.mark.asyncio
    async def test_no_key_disables_voice(self, monkeypatch, stale_env):
        from app.core.exceptions import ProviderUnavailableError
        from app.services.voice import openai_voice_client as mod

        _wipe("openai")
        assert await mod.voice_enabled() is False
        with pytest.raises(ProviderUnavailableError):
            await mod.transcribe(
            b"audio-bytes", filename="a.webm", content_type="audio/webm"
        )
        with pytest.raises(ProviderUnavailableError):
            await mod.synthesize("hello")


# ---------------------------------------------------------------------------
# AI review
# ---------------------------------------------------------------------------


class TestAiReviewFollowsTheResolver:
    @pytest.mark.asyncio
    async def test_falls_back_to_the_provider_pool_and_follows_a_rotation(
        self, stale_env
    ):
        from app.models.ai_review import AiReviewSettingsORM
        from app.services.ai_review_provider import resolve_review_key

        row = AiReviewSettingsORM(review_provider="anthropic", review_api_key_enc=None)

        _set_key("anthropic", ANTHROPIC_KEY)
        assert await resolve_review_key(row) == ANTHROPIC_KEY

        rotated = "sk-ant-api03-" + "4" * 60
        _set_key("anthropic", rotated)
        assert await resolve_review_key(row) == rotated

        _wipe("anthropic")
        assert await resolve_review_key(row) is None

    @pytest.mark.asyncio
    async def test_a_dedicated_review_key_still_wins(self, stale_env):
        """The most specific answer stays the most specific answer."""
        from app.models.ai_review import AiReviewSettingsORM
        from app.services.ai_review_provider import encode_key, resolve_review_key

        dedicated = "sk-ant-api03-" + "5" * 60
        row = AiReviewSettingsORM(
            review_provider="anthropic", review_api_key_enc=encode_key(dedicated)
        )
        _set_key("anthropic", ANTHROPIC_KEY)

        assert await resolve_review_key(row) == dedicated

    @pytest.mark.asyncio
    async def test_a_custom_provider_still_requires_its_own_key(self, stale_env):
        from app.models.ai_review import AiReviewSettingsORM
        from app.services.ai_review_provider import resolve_review_key

        row = AiReviewSettingsORM(review_provider="custom", review_api_key_enc=None)
        _set_key("openai", FIRST_KEY)

        assert await resolve_review_key(row) is None
