"""FIX51 Part 2.2 — the mock search engine is a dev lever, never a prod offer.

The ``mock`` adapter exists so the whole search path can be driven with
no real key (proven live: tenant gate → /v1/search → mock engine →
web_search tool → search_sources citations). These tests pin the
guardrail: it registers outside production and NEVER in production —
the admin "add provider" dropdown validates against SUPPORTED_PROVIDERS,
so a prod deployment must not see it.
"""

import importlib


def test_mock_engine_present_outside_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    import app.services.search.providers as sp

    reloaded = importlib.reload(sp)
    try:
        assert "mock" in reloaded.SUPPORTED_PROVIDERS
        assert "tavily" in reloaded.SUPPORTED_PROVIDERS
        assert "brave" in reloaded.SUPPORTED_PROVIDERS
    finally:
        importlib.reload(sp)


def test_mock_engine_absent_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    import app.services.search.providers as sp

    reloaded = importlib.reload(sp)
    try:
        assert "mock" not in reloaded.SUPPORTED_PROVIDERS
        # The real engines are unaffected by the gate.
        assert set(reloaded.SUPPORTED_PROVIDERS) >= {
            "tavily",
            "brave",
            "serpapi",
            "google",
            "bing",
        }
    finally:
        monkeypatch.setenv("APP_ENV", "test")
        importlib.reload(sp)
