"""FIX126 U1 — web-search results link to the publisher, not news.google.com.

Recorded fixtures only (no network): a Tavily-shaped reply whose news
items are Google News RSS redirects — the NEW ``AU_yqL`` id format seen
live on 2026-09-21 (scripts/probes/r2/results/c-websearch.json) and the
OLD format whose id carries the URL itself — plus the article page and
``batchexecute`` reply Google serves for the new format.
"""

from __future__ import annotations

import asyncio
import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from app.services.search.base import SearchResult, SearchResults

# The live redirect (new format) and the article it stands for.
NEW_ID = (
    "CBMiZkFVX3lxTE5IVWIyNkZCRzgzazRUNHZqN1U4OTNPeVo2dVZLM1pUSkw1Q3o0UGxYVXJsQ0tF"
    "anlXZ2FMbGZDUDdWcmxrVEV4STFFV2pfNmd6SEY3Wl85aldUUUk4dDJRQnI2VmZidw"
)
NEW_URL = f"https://news.google.com/rss/articles/{NEW_ID}?oc=5"
NEW_TARGET = "https://www.bangkokpost.com/business/general/3100001/fed-holds-rates"

OLD_TARGET = "https://www.reuters.com/markets/us/fed-signals-cut-2026-09-20/"


def _old_id(url: str) -> str:
    body = url.encode()
    raw = b"\x08\x13\x22" + bytes([len(body)]) + body + b"\xd2\x01\x00"
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


OLD_URL = f"https://news.google.com/rss/articles/{_old_id(OLD_TARGET)}?oc=5"
PLAIN_URL = "https://www.thairath.co.th/news/money/2800001"

TAVILY_REPLY = {
    "query": "ข่าว Fed",
    "results": [
        {"title": "Fed holds rates - Bangkok Post", "url": NEW_URL, "content": "…"},
        {"title": "Fed signals cut - Reuters", "url": OLD_URL, "content": "…"},
        {"title": "เฟดคงดอกเบี้ย", "url": PLAIN_URL, "content": "…"},
    ],
}

ARTICLE_PAGE = (
    "<html><body><c-wiz><div jscontroller=\"aLI87\" "
    'data-n-a-id="' + NEW_ID + '" data-n-a-ts="1758436800" '
    'data-n-a-sg="AZ5r3eT_recordedSignature"></div></c-wiz></body></html>'
)
BATCH_REPLY = ")]}'\n\n" + json.dumps(
    [
        ["wrb.fr", "Fbv4je", json.dumps(["garturlres", NEW_TARGET, 1]), None, None, None, "generic"],
        ["di", 42],
        ["af.httprm", 41, "-1234", 8],
    ]
) + "\n"

GN_PAGE = f"https://news.google.com/rss/articles/{NEW_ID}"
GN_BATCH = "https://news.google.com/_/DotsSplashUi/data/batchexecute"


@pytest.fixture
def router():
    # Every HTTP call must hit a recorded route (assert_all_mocked): the
    # suite never reaches the network.
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as m:
        yield m


@pytest.fixture(autouse=True)
def _fresh_cache():
    from app.services.search import url_resolve

    url_resolve.clear_cache()
    yield
    url_resolve.clear_cache()


def _results() -> SearchResults:
    return SearchResults(
        query="q",
        provider="tavily",
        results=[
            SearchResult(title=r["title"], url=r["url"], snippet=r["content"])
            for r in TAVILY_REPLY["results"]
        ],
    )


class _FakeAdapter:
    async def search(self, query: str, max_results: int) -> SearchResults:
        return _results()


def _cfg():
    return SimpleNamespace(
        provider="tavily",
        health_status="healthy",
        api_key_encrypted="x",
        max_results=5,
        total_queries=0,
        total_failures=0,
        avg_latency_ms=None,
        uptime_pct=100.0,
        last_health_check=None,
    )


# ---------------------------------------------------------------------------
# The whole search path: what the chat tool receives.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_service_returns_publisher_urls(monkeypatch, router):
    from app.services.search import service as svc_mod

    router.get(GN_PAGE).mock(return_value=httpx.Response(200, text=ARTICLE_PAGE))
    router.post(GN_BATCH).mock(return_value=httpx.Response(200, text=BATCH_REPLY))

    svc = svc_mod.SearchService()
    monkeypatch.setattr(svc, "get_enabled_providers", AsyncMock(return_value=[_cfg()]))
    monkeypatch.setattr(svc_mod, "get_adapter", lambda provider, key: _FakeAdapter())
    monkeypatch.setattr(svc_mod, "decode_key", lambda enc: "k")
    session = SimpleNamespace(flush=AsyncMock())

    out = await svc.search(session, "t1", "ข่าว Fed")

    urls = [r.url for r in out.results]
    assert urls == [NEW_TARGET, OLD_TARGET, PLAIN_URL]
    assert [r.domain for r in out.results] == ["bangkokpost.com", "reuters.com", "thairath.co.th"]
    assert not any("news.google.com" in json.dumps(r.to_dict()) for r in out.results)


# ---------------------------------------------------------------------------
# The resolver's parts.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_old_format_decodes_without_network(router):
    # assert_all_mocked + no routes: any HTTP call would raise.
    from app.services.search.url_resolve import resolve_url

    assert await resolve_url(OLD_URL) == OLD_TARGET


def test_new_format_is_not_decodable_offline():
    from app.services.search.url_resolve import decode_offline, google_news_id

    assert decode_offline(google_news_id(NEW_URL)) is None
    assert google_news_id(PLAIN_URL) is None


@pytest.mark.asyncio
async def test_resolve_failure_keeps_the_redirect(router):
    from app.services.search.url_resolve import resolve_results

    router.get(GN_PAGE).mock(return_value=httpx.Response(500, text="nope"))
    out = await resolve_results(_results())
    # The new-format item could not be resolved → its redirect stays; the
    # old-format one needed no network and still resolves.
    assert [r.url for r in out.results] == [NEW_URL, OLD_TARGET, PLAIN_URL]


@pytest.mark.asyncio
async def test_transport_error_keeps_the_redirect(router):
    from app.services.search.url_resolve import resolve_results

    router.get(GN_PAGE).mock(side_effect=httpx.ConnectError("down"))
    out = await resolve_results(_results())
    assert out.results[0].url == NEW_URL


@pytest.mark.asyncio
async def test_garbled_batchexecute_keeps_the_redirect(router):
    from app.services.search.url_resolve import resolve_results

    router.get(GN_PAGE).mock(return_value=httpx.Response(200, text=ARTICLE_PAGE))
    router.post(GN_BATCH).mock(return_value=httpx.Response(200, text=")]}'\n\nnot json"))
    out = await resolve_results(_results())
    assert out.results[0].url == NEW_URL


@pytest.mark.asyncio
async def test_slow_resolve_never_holds_the_answer(router):
    from app.services.search.url_resolve import resolve_results

    async def _slow(request):
        await asyncio.sleep(2)
        return httpx.Response(200, text=ARTICLE_PAGE)

    router.get(GN_PAGE).mock(side_effect=_slow)
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    out = await resolve_results(_results(), budget_s=0.1)
    assert loop.time() - t0 < 1.0
    assert out.results[0].url == NEW_URL
    assert out.results[1].url == OLD_TARGET


@pytest.mark.asyncio
async def test_resolution_is_cached(router):
    from app.services.search.url_resolve import resolve_url

    page = router.get(GN_PAGE).mock(return_value=httpx.Response(200, text=ARTICLE_PAGE))
    batch = router.post(GN_BATCH).mock(return_value=httpx.Response(200, text=BATCH_REPLY))
    assert await resolve_url(NEW_URL) == NEW_TARGET
    assert await resolve_url(NEW_URL) == NEW_TARGET
    assert page.call_count == 1 and batch.call_count == 1


def test_batchexecute_request_carries_only_googles_article_id():
    """What leaves for Google is its own id + signature, no query text."""
    from app.services.search import url_resolve

    captured = {}

    async def _run():
        with respx.mock(assert_all_called=False) as m:
            m.get(GN_PAGE).mock(return_value=httpx.Response(200, text=ARTICLE_PAGE))

            def _grab(request):
                captured["body"] = request.content.decode()
                return httpx.Response(200, text=BATCH_REPLY)

            m.post(GN_BATCH).mock(side_effect=_grab)
            return await url_resolve.resolve_url(NEW_URL)

    assert asyncio.run(_run()) == NEW_TARGET
    from urllib.parse import unquote

    body = unquote(captured["body"])
    assert NEW_ID in body and "AZ5r3eT_recordedSignature" in body and "1758436800" in body
    assert "Fed" not in body
