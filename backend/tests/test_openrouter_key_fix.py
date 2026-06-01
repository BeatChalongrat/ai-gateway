"""Regression tests for the OpenRouter placeholder-key incident.

Root cause recap: a placeholder ``OPENROUTER_API_KEY=sk-or-...`` was
imported into ``provider_keys`` as an active, untested row. Key resolution
only excluded ``last_test_status == 'invalid'``, so the stub stayed in the
round-robin pool and 401'd ~half the OpenRouter dispatches; the streaming
path then returned an empty 200 body (blank screen). The admin "Test"
button hid it because it pinged the PUBLIC ``/api/v1/models`` endpoint,
which 200s for any bearer.

Covers:
* B1 — placeholder keys persisted in the table are filtered out of the
  dispatch pool (without dropping legitimately-short keys).
* B2 — the OpenRouter key test hits the auth-REQUIRED ``/api/v1/key``
  endpoint, so a bad key reports invalid.
* B3 — a provider 401 quarantines the offending key and the dispatcher
  retries the same provider with the next key in the pool.
"""

import base64
import uuid

import httpx
import pytest
import respx

from app.core.exceptions import ProviderError
from app.domain.entities import RouteDecision
from app.domain.providers import ProviderResponse
from app.models.provider_key import ProviderKeyORM
from app.schemas.common import Message
from app.schemas.public import ChatCompletionsRequest
from app.services.provider_dispatcher import ProviderDispatcher
from app.services.provider_key_service import (
    ProviderKeyService,
    _filter_table_candidates,
    _is_placeholder_table_key,
    clear_keys_cache,
)
import app.models  # noqa: F401


def _enc(raw: str) -> str:
    return base64.b64encode(raw.encode()).decode()


class _FakeRow:
    """Minimal stand-in for a ProviderKeyORM row for the filter unit test."""

    def __init__(self, key, status="valid", label="L"):
        self.api_key_encrypted = _enc(key)
        self.last_test_status = status
        self.id = uuid.uuid4()
        self.label = label


# ─────────────────────────── B1 ───────────────────────────


class TestPlaceholderFiltering:
    def test_placeholder_marker_detected(self):
        assert _is_placeholder_table_key("sk-or-...") is True
        assert _is_placeholder_table_key("changeme") is True
        assert _is_placeholder_table_key("your-key-here") is True
        assert _is_placeholder_table_key("") is True
        assert _is_placeholder_table_key("   ") is True

    def test_real_key_not_flagged(self):
        # A genuine OpenRouter key — and even a SHORT fake key with no
        # placeholder marker — must NOT be flagged (the table filter does
        # not apply the env-var len<20 heuristic).
        assert _is_placeholder_table_key("sk-or-v1-" + "a" * 64) is False
        assert _is_placeholder_table_key("sk-secret-123") is False

    def test_filter_drops_placeholder_keeps_valid(self):
        rows = [
            _FakeRow("sk-or-...", status="untested", label="Imported from environment"),
            _FakeRow("sk-or-v1-realkey-" + "z" * 40, status="valid", label="Key 3"),
        ]
        out = _filter_table_candidates(rows, "openrouter")
        assert out == ["sk-or-v1-realkey-" + "z" * 40]

    def test_filter_drops_invalid_status(self):
        rows = [
            _FakeRow("sk-or-v1-aaa-" + "a" * 40, status="invalid"),
            _FakeRow("sk-or-v1-bbb-" + "b" * 40, status="valid"),
        ]
        out = _filter_table_candidates(rows, "openrouter")
        assert out == ["sk-or-v1-bbb-" + "b" * 40]

    @pytest.mark.asyncio
    async def test_resolution_skips_placeholder_row(self, async_db):
        clear_keys_cache()
        provider = f"orprov-{uuid.uuid4().hex[:8]}"
        async_db.add(
            ProviderKeyORM(
                id=str(uuid.uuid4()),
                provider_name=provider,
                label="Imported from environment",
                api_key_encrypted=_enc("sk-or-..."),
                is_active=True,
                last_test_status="untested",
            )
        )
        real = "sk-or-v1-" + "r" * 60
        async_db.add(
            ProviderKeyORM(
                id=str(uuid.uuid4()),
                provider_name=provider,
                label="Key 3",
                api_key_encrypted=_enc(real),
                is_active=True,
                last_test_status="valid",
            )
        )
        await async_db.flush()

        # Round-robin a few times — every resolution must return the real
        # key, never the placeholder (which previously surfaced ~50%).
        svc = ProviderKeyService()
        for _ in range(5):
            assert await svc.get_active_key_async(async_db, provider) == real
        clear_keys_cache()


# ─────────────────────────── B2 ───────────────────────────


class TestOpenRouterKeyTestEndpoint:
    @respx.mock
    @pytest.mark.asyncio
    async def test_uses_key_endpoint_not_public_models(self):
        key_route = respx.get("https://openrouter.ai/api/v1/key").mock(
            return_value=httpx.Response(200, json={"data": {"limit": 10}})
        )
        models_route = respx.get("https://openrouter.ai/api/v1/models").mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        result = await ProviderKeyService().test_key("openrouter", "sk-or-v1-good")
        assert result["valid"] is True
        # The whole point of B2: validate against the auth-gated endpoint,
        # never the public catalog that 200s for any bearer.
        assert key_route.called
        assert not models_route.called

    @respx.mock
    @pytest.mark.asyncio
    async def test_bad_key_reports_invalid(self):
        respx.get("https://openrouter.ai/api/v1/key").mock(
            return_value=httpx.Response(401, json={"error": {"message": "no auth"}})
        )
        result = await ProviderKeyService().test_key("openrouter", "sk-or-...")
        assert result["valid"] is False
        assert "401" in result["error"]


# ─────────────────────────── B3 ───────────────────────────

# The conftest autouse fixture stubs ``dispatch_with_fallback``; override
# it locally so these tests exercise the REAL dispatcher.
@pytest.fixture(autouse=True)
def _mock_provider_dispatch():  # pragma: no cover — fixture wiring
    yield


def _payload():
    return ChatCompletionsRequest(
        model="deepseek/deepseek-r1",
        messages=[Message(role="user", content="hi")],
    )


def _messages():
    return [Message(role="user", content="hi")]


def _route(primary="openrouter", fallbacks=None):
    return RouteDecision(
        provider_name=primary,
        model_name="deepseek/deepseek-r1",
        fallbacks=[(p, f"m_{p}") for p in (fallbacks or [])],
    )


class TestAutoInvalidationOn401:
    @pytest.mark.asyncio
    async def test_quarantines_and_retries_next_key(self):
        dispatcher = ProviderDispatcher()
        keys_seen: list[str] = []

        async def chat(self, payload, messages, *, resolved_model=None, api_key=None):
            keys_seen.append(api_key)
            if api_key == "bad-key":
                raise ProviderError(
                    "Provider 'openrouter' rejected credentials (401).",
                    details={"provider": "openrouter", "status": 401},
                )
            return ProviderResponse(content="ok", model="m")

        for adapter in dispatcher.adapters.values():
            adapter.chat = chat.__get__(adapter)

        invalidated: list[tuple[str, str]] = []

        def on_auth_failure(provider, failed_key):
            invalidated.append((provider, failed_key))
            return "good-key"

        resp, used_provider, used_model = await dispatcher.dispatch_with_fallback(
            _route("openrouter"),
            _payload(),
            _messages(),
            api_key="bad-key",
            key_resolver=lambda p: None,
            on_auth_failure=on_auth_failure,
        )

        assert resp.content == "ok"
        assert used_provider == "openrouter"
        # First the bad key (401), then the replacement returned by the
        # callback — same provider, no cross-provider hop.
        assert keys_seen == ["bad-key", "good-key"]
        assert invalidated == [("openrouter", "bad-key")]

    @pytest.mark.asyncio
    async def test_no_replacement_key_propagates_error(self):
        dispatcher = ProviderDispatcher()
        calls: list[str] = []

        async def chat(self, payload, messages, *, resolved_model=None, api_key=None):
            calls.append(api_key)
            raise ProviderError(
                "Provider 'openrouter' rejected credentials (401).",
                details={"provider": "openrouter", "status": 401},
            )

        for adapter in dispatcher.adapters.values():
            adapter.chat = chat.__get__(adapter)

        invalidated: list[str] = []

        def on_auth_failure(provider, failed_key):
            invalidated.append(failed_key)
            return None  # pool exhausted — no other key

        # No fallbacks configured → the 401 (provider_error, eligible) has
        # nowhere to go and propagates.
        with pytest.raises(ProviderError):
            await dispatcher.dispatch_with_fallback(
                _route("openrouter"),
                _payload(),
                _messages(),
                api_key="bad-key",
                key_resolver=lambda p: None,
                on_auth_failure=on_auth_failure,
            )
        # The bad key was still quarantined even though no retry happened.
        assert invalidated == ["bad-key"]
        # Only one dispatch — no retry without a replacement key.
        assert calls == ["bad-key"]


class TestMarkInvalidByValue:
    @pytest.mark.asyncio
    async def test_async_flips_matching_key(self, async_db):
        clear_keys_cache()
        provider = f"miprov-{uuid.uuid4().hex[:8]}"
        bad = "sk-or-v1-bad-" + "x" * 40
        good = "sk-or-v1-good-" + "y" * 40
        async_db.add_all(
            [
                ProviderKeyORM(
                    id=str(uuid.uuid4()),
                    provider_name=provider,
                    label="bad",
                    api_key_encrypted=_enc(bad),
                    is_active=True,
                    last_test_status="valid",
                ),
                ProviderKeyORM(
                    id=str(uuid.uuid4()),
                    provider_name=provider,
                    label="good",
                    api_key_encrypted=_enc(good),
                    is_active=True,
                    last_test_status="valid",
                ),
            ]
        )
        await async_db.flush()

        svc = ProviderKeyService()
        flipped = await svc.mark_invalid_by_value_async(async_db, provider, bad)
        assert flipped is True
        await async_db.flush()

        # The quarantined key drops out; only the good one resolves.
        clear_keys_cache()
        for _ in range(4):
            assert await svc.get_active_key_async(async_db, provider) == good
        clear_keys_cache()
