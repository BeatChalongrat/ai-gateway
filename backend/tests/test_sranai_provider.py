"""P1 — self-hosted "Sran AI" provider: base-URL resolution, SSRF gate,
key probe, dispatcher registration, and pool eviction.

The DB layer of ``resolve_base_url`` is monkeypatched at the
``_read_db_override`` seam so these tests need no database.
"""

from __future__ import annotations

import time

import httpx
import pytest
import respx

from app.core.exceptions import ProviderUnavailableError, ValidationError
from app.services.providers import base_urls
from app.services.providers.base_urls import (
    resolve_base_url,
    validate_provider_base_url,
)
from app.services.provider_key_service import ProviderKeyService


@pytest.fixture(autouse=True)
def _clean_base_url_cache():
    base_urls.clear_base_url_cache()
    yield
    base_urls.clear_base_url_cache()


# ---------------------------------------------------------------------------
# resolve_base_url priority: DB → default (no env tier — UI-only config)
# ---------------------------------------------------------------------------

def _seed_db_cache(provider: str, value: str | None) -> None:
    base_urls._db_cache[provider] = (time.monotonic() + 30.0, value)


def test_db_value_wins_over_default():
    _seed_db_cache("sranai", "http://box:8000/v1")
    assert resolve_base_url("sranai", "http://default/v1") == "http://box:8000/v1"


def test_no_env_tier_between_db_and_default(monkeypatch):
    """PROVIDER_BASE_URL_OVERRIDES is no longer consulted. Even with the
    variable live in the process environment, an unset DB row falls straight
    through to the adapter default — endpoints are admin-UI-only."""
    monkeypatch.setenv(
        "PROVIDER_BASE_URL_OVERRIDES", '{"sranai": "http://env:9000/v1"}'
    )
    _seed_db_cache("sranai", None)
    assert resolve_base_url("sranai", "http://default/v1") == "http://default/v1"
    # Self-hosted with no default and no DB row stays unconfigured ("").
    assert resolve_base_url("sranai", "") == ""


def test_env_tier_is_gone_by_construction():
    """PROVIDER_BASE_URL_OVERRIDES is a first-boot SEED, never a resolve-time
    tier.

    The distinction is the whole design: ``base_urls`` does not import the
    settings object at all, so no amount of environment editing on a running
    system can change where a provider is called. The env value is read once,
    by ``provider_env_seed``, and written to the ``system_settings`` row that
    ``resolve_base_url`` already reads.

    The old back-door property is also gone — a parsed map hanging off
    ``Settings`` is exactly the thing a future caller would reach for.
    """
    import app.services.providers.base_urls as mod
    from app.core.config import settings as app_settings

    assert not hasattr(mod, "settings")
    assert not hasattr(app_settings, "provider_base_url_overrides_map")
    # The seed input exists, and is a plain unparsed string.
    assert isinstance(app_settings.provider_base_url_overrides, str)


def test_the_seed_does_not_become_a_resolution_tier(monkeypatch):
    """Setting the env var must not change resolution on its own."""
    from app.core.config import settings as app_settings
    from app.services.providers.base_urls import clear_base_url_cache, resolve_base_url

    monkeypatch.setattr(
        app_settings,
        "provider_base_url_overrides",
        '{"openai": "http://not-used:9090/v1"}',
        raising=False,
    )
    clear_base_url_cache()

    assert resolve_base_url("openai", "https://api.openai.com/v1") == (
        "https://api.openai.com/v1"
    )


def test_default_when_no_overrides(monkeypatch):
    _seed_db_cache("openai", None)
    assert resolve_base_url("openai", "https://api.openai.com/v1/") == (
        "https://api.openai.com/v1"
    )


def test_empty_db_value_falls_through():
    # An empty system_settings value (the seeded placeholder) must be
    # cached as None by _read_db_override, never returned as "".
    _seed_db_cache("sranai", None)
    assert resolve_base_url("sranai", "http://default/v1") == "http://default/v1"


def test_clear_cache_forces_reread(monkeypatch):
    _seed_db_cache("sranai", "http://old:1/v1")
    assert resolve_base_url("sranai", "") == "http://old:1/v1"
    base_urls.clear_base_url_cache()
    monkeypatch.setattr(base_urls, "_read_db_override", lambda name: "http://new:2/v1")
    assert resolve_base_url("sranai", "") == "http://new:2/v1"


def test_db_read_failure_fails_open(monkeypatch):
    # Simulate an unreachable DB inside the real _read_db_override by
    # pointing SessionLocal at a factory that raises.
    class _Boom:
        def __call__(self):
            raise RuntimeError("db down")

    import app.core.db as core_db

    monkeypatch.setattr(core_db, "SessionLocal", _Boom())
    assert resolve_base_url("sranai", "http://default/v1") == "http://default/v1"


# ---------------------------------------------------------------------------
# SSRF gate (lenient: private/internal allowed, link-local blocked)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "url,expected",
    [
        ("http://10.0.0.5:8000/v1", "http://10.0.0.5:8000/v1"),
        ("http://192.168.1.20:11434/v1/", "http://192.168.1.20:11434/v1"),
        ("http://172.16.0.9:8000/v1", "http://172.16.0.9:8000/v1"),
        ("http://host.docker.internal:8000/v1", "http://host.docker.internal:8000/v1"),
        ("http://localhost:8000/v1", "http://localhost:8000/v1"),
        ("https://sran-box.gbtech.co.th/v1", "https://sran-box.gbtech.co.th/v1"),
        ("  http://box:8000/v1  ", "http://box:8000/v1"),
    ],
)
def test_gate_allows_private_and_internal(url, expected):
    assert validate_provider_base_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "",
        "not a url",
        "ftp://10.0.0.5/v1",
        "file:///etc/passwd",
        "http://169.254.169.254/latest/meta-data",
        "http://169.254.7.7:8000/v1",
        "http://[fe80::1]:8000/v1",
        "http://metadata.google.internal/computeMetadata/v1",
        "http:///v1",
    ],
)
def test_gate_rejects_bad_urls(url):
    with pytest.raises(ValidationError):
        validate_provider_base_url(url)


# ---------------------------------------------------------------------------
# test_key: sranai branch (OpenAI-compatible probe)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sranai_no_endpoint_reports_not_configured(monkeypatch):
    _seed_db_cache("sranai", None)
    svc = ProviderKeyService()
    result = await svc.test_key("sranai", "sran-key-1234567890")
    assert result["valid"] is False
    assert "endpoint not configured" in result["error"]


@pytest.mark.asyncio
@respx.mock
async def test_sranai_valid_via_payload_base_url():
    route = respx.get("http://box:8000/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "qwen2.5-32b-instruct"}]})
    )
    svc = ProviderKeyService()
    result = await svc.test_key(
        "sranai", "sran-key-1234567890", base_url="http://box:8000/v1/"
    )
    assert route.called
    assert result["valid"] is True


@pytest.mark.asyncio
@respx.mock
async def test_sranai_invalid_key_401():
    respx.get("http://box:8000/v1/models").mock(
        return_value=httpx.Response(401, json={"error": {"message": "bad key"}})
    )
    svc = ProviderKeyService()
    result = await svc.test_key(
        "sranai", "wrong-key-1234567890", base_url="http://box:8000/v1"
    )
    assert result["valid"] is False
    assert "invalid key" in result["error"]


@pytest.mark.asyncio
@respx.mock
async def test_sranai_unreachable_box_static_error():
    respx.get("http://box:8000/v1/models").mock(
        side_effect=httpx.ConnectError("boom")
    )
    svc = ProviderKeyService()
    result = await svc.test_key(
        "sranai", "sran-key-1234567890", base_url="http://box:8000/v1"
    )
    assert result["valid"] is False
    # Token-isolation rule: static label, no raw exception text.
    assert result["error"] == "connection error — endpoint unreachable"


@pytest.mark.asyncio
@respx.mock
async def test_sranai_retest_uses_stored_endpoint(monkeypatch):
    # Re-test route passes no base_url — the probe must resolve the
    # stored (DB-cached) endpoint.
    _seed_db_cache("sranai", "http://stored:8000/v1")
    route = respx.get("http://stored:8000/v1/models").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    svc = ProviderKeyService()
    result = await svc.test_key("sranai", "sran-key-1234567890")
    assert route.called
    assert result["valid"] is True


@pytest.mark.asyncio
async def test_unknown_provider_still_rejected():
    svc = ProviderKeyService()
    result = await svc.test_key("nonsense", "whatever-key-123456")
    assert result["valid"] is False
    # FIX126 O8 — the structured key-test result (841604a) words it
    # "unknown provider: <name>"; the rejection is what matters, not the case.
    assert "unknown provider" in result["error"].lower()


# ---------------------------------------------------------------------------
# Dispatcher registration + unconfigured-endpoint adapter guard
# ---------------------------------------------------------------------------

def test_sranai_registered_in_dispatcher():
    from app.services.provider_dispatcher import ProviderDispatcher
    from app.services.providers.sranai_adapter import SranAIAdapter

    dispatcher = ProviderDispatcher()
    assert isinstance(dispatcher.adapters.get("sranai"), SranAIAdapter)
    assert dispatcher.adapters["sranai"].supports_streaming is True


@pytest.mark.asyncio
async def test_adapter_raises_clean_error_when_unconfigured(monkeypatch):
    import app.services.providers.sranai_adapter as mod

    # FIX85 — the adapter asks ONE function, with the key in hand.
    monkeypatch.setattr(mod, "effective_base_url", lambda name, default, api_key=None: "")
    adapter = mod.SranAIAdapter()
    with pytest.raises(ProviderUnavailableError):
        await adapter._get_client()


@pytest.mark.asyncio
async def test_adapter_gets_pooled_client_when_configured(monkeypatch):
    import app.services.providers.sranai_adapter as mod
    import app.services.providers.openai_compatible_adapter as base_mod

    # Both the guard (sranai_adapter) and the base _get_client
    # (openai_compatible_adapter) import effective_base_url into their own
    # namespace — patch both so they agree, as they do in production.
    monkeypatch.setattr(mod, "effective_base_url", lambda name, default, api_key=None: "http://box:8000/v1")
    monkeypatch.setattr(base_mod, "effective_base_url", lambda name, default, api_key=None: "http://box:8000/v1")
    adapter = mod.SranAIAdapter()
    client = await adapter._get_client()
    assert str(client.base_url).rstrip("/") == "http://box:8000/v1"


# ---------------------------------------------------------------------------
# httpx pool eviction on endpoint change
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_evict_client_closes_and_removes():
    from app.services.providers import http_client

    client = await http_client.get_provider_client(base_url="http://old-box:8000/v1")
    assert "http://old-box:8000/v1" in http_client._clients

    await http_client.evict_client("http://old-box:8000/v1")
    assert "http://old-box:8000/v1" not in http_client._clients
    assert client.is_closed


@pytest.mark.asyncio
async def test_evict_client_missing_url_is_noop():
    from app.services.providers import http_client

    await http_client.evict_client("http://never-created:1/v1")  # must not raise
