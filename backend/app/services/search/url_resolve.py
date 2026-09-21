"""FIX126 U1 — turn Google News redirect links into the publisher's URL.

Search engines (Tavily and friends index Google News' RSS feed) return
news items as ``https://news.google.com/rss/articles/CBMi…?oc=5`` — a
Google redirect, not the article. The citation chip then shows
"news.google.com" and a click lands on a Google interstitial. This module
resolves those links on the server, before the results reach the model
or the page.

Two formats exist:

* **Old** — the ``CBMi…`` id is URL-safe base64 of a small protobuf whose
  string field IS the article URL. Decoded here with no network.
* **New** (every link seen live in 2026) — the protobuf holds an opaque
  ``AU_yqL…`` token. Only Google can map it, through the same two calls
  the news.google.com page makes: GET the article page for its signature
  + timestamp, then POST ``batchexecute`` (``Fbv4je`` / ``garturlreq``).
  What leaves is Google's own article id — no user content.

The network half is bounded so it can never hold the answer up: a short
per-call timeout, at most ``_MAX_CONCURRENCY`` resolutions in flight
process-wide, a wall-clock budget for the whole result list (whatever is
not resolved by then keeps its redirect), and a cache (hits and misses)
so a link is paid for once. Any failure keeps the original URL.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import os
import re
import time
from collections import OrderedDict
from dataclasses import replace
from html import unescape
from typing import Callable
from urllib.parse import quote, urlparse

import httpx
import structlog

from app.services.search.base import SearchResult, SearchResults, domain_of

logger = structlog.get_logger(__name__)

_GN_HOST = "news.google.com"
# /rss/articles/<id>, /articles/<id>, /read/<id> — the id is the last
# path segment in every variant Google serves.
_GN_PATH = re.compile(r"^/(?:rss/)?(?:articles|read)/([A-Za-z0-9_-]+)")

_CALL_TIMEOUT = httpx.Timeout(2.5, connect=1.5)
_BUDGET_S = float(os.environ.get("SEARCH_URL_RESOLVE_BUDGET_S", "3.0"))
_MAX_CONCURRENCY = 4
_CACHE_MAX = 2048
_CACHE_TTL_OK_S = 7 * 24 * 3600
_CACHE_TTL_MISS_S = 10 * 60
_BATCH_URL = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_semaphore: asyncio.Semaphore | None = None
_semaphore_loop: asyncio.AbstractEventLoop | None = None
# url -> (expires_at, resolved url or None for a remembered miss)
_cache: "OrderedDict[str, tuple[float, str | None]]" = OrderedDict()


def _enabled() -> bool:
    return os.environ.get("SEARCH_RESOLVE_GOOGLE_NEWS", "true").strip().lower() not in (
        "0", "false", "no", "off",
    )


def _http_enabled() -> bool:
    return os.environ.get("SEARCH_RESOLVE_GOOGLE_NEWS_HTTP", "true").strip().lower() not in (
        "0", "false", "no", "off",
    )


def google_news_id(url: str) -> str | None:
    """The article id of a Google News redirect link, else ``None``."""
    try:
        p = urlparse(url)
    except (ValueError, TypeError):
        return None
    if (p.hostname or "").lower() != _GN_HOST:
        return None
    m = _GN_PATH.match(p.path or "")
    return m.group(1) if m else None


def _is_http_url(s: str) -> bool:
    try:
        p = urlparse(s)
    except ValueError:
        return False
    return p.scheme in ("http", "https") and bool(p.hostname) and (p.hostname or "").lower() != _GN_HOST


def decode_offline(article_id: str) -> str | None:
    """Decode an OLD-format id to the article URL, without network.

    The id is URL-safe base64 of ``08 13 22 <varint len> <url> …``. A new-
    format id decodes to ``AU_yqL…`` instead of a URL → ``None``.
    """
    try:
        raw = base64.urlsafe_b64decode(article_id + "=" * (-len(article_id) % 4))
    except (binascii.Error, ValueError):
        return None
    prefix = b"\x08\x13\x22"
    if raw.startswith(prefix):
        raw = raw[len(prefix):]
        # varint length of the string field
        length = 0
        shift = 0
        i = 0
        while i < len(raw) and i < 5:
            b = raw[i]
            length |= (b & 0x7F) << shift
            i += 1
            if not b & 0x80:
                break
            shift += 7
        body = raw[i:i + length] if length else raw[i:]
    else:
        body = raw
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if text.startswith("AU_yqL"):
        return None
    m = re.match(r"https?://[^\s\x00-\x1f\"<>]+", text)
    if m and _is_http_url(m.group(0)):
        return m.group(0)
    return None


def _cache_get(url: str) -> tuple[bool, str | None]:
    hit = _cache.get(url)
    if hit is None:
        return False, None
    expires, value = hit
    if expires < time.monotonic():
        _cache.pop(url, None)
        return False, None
    _cache.move_to_end(url)
    return True, value


def _cache_put(url: str, value: str | None) -> None:
    ttl = _CACHE_TTL_OK_S if value else _CACHE_TTL_MISS_S
    _cache[url] = (time.monotonic() + ttl, value)
    _cache.move_to_end(url)
    while len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)


def clear_cache() -> None:
    _cache.clear()


def _get_semaphore() -> asyncio.Semaphore:
    # One semaphore per event loop (tests run each on its own loop).
    global _semaphore, _semaphore_loop
    loop = asyncio.get_running_loop()
    if _semaphore is None or _semaphore_loop is not loop:
        _semaphore = asyncio.Semaphore(_MAX_CONCURRENCY)
        _semaphore_loop = loop
    return _semaphore


_SG = re.compile(r'data-n-a-sg="([^"]+)"')
_TS = re.compile(r'data-n-a-ts="([^"]+)"')


async def _resolve_online(client: httpx.AsyncClient, article_id: str) -> str | None:
    """New-format id → article URL via Google's own two calls."""
    page = await client.get(
        f"https://{_GN_HOST}/rss/articles/{article_id}", headers={"User-Agent": _UA}
    )
    if page.status_code != 200:
        return None
    sg = _SG.search(page.text)
    ts = _TS.search(page.text)
    if not (sg and ts):
        return None
    signature, timestamp = unescape(sg.group(1)), unescape(ts.group(1))
    if not timestamp.isdigit():
        return None
    inner = (
        '["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,'
        'null,null,null,0,1],"X","X",1,[1,1,1],1,1,null,0,0,null,0],'
        f'{json.dumps(article_id)},{timestamp},{json.dumps(signature)}]'
    )
    body = "f.req=" + quote(json.dumps([[["Fbv4je", inner]]]))
    resp = await client.post(
        _BATCH_URL,
        content=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            "User-Agent": _UA,
        },
    )
    if resp.status_code != 200:
        return None
    return parse_batchexecute(resp.text)


def parse_batchexecute(text: str) -> str | None:
    """Pull the article URL out of a ``garturlreq`` batchexecute reply."""
    try:
        # ``)]}'`` guard, blank line, then the envelope array.
        chunk = text.split("\n\n")[1]
        envelopes = json.loads(chunk)
        for env in envelopes:
            if isinstance(env, list) and len(env) > 2 and env[0] == "wrb.fr" and env[2]:
                inner = json.loads(env[2])
                if isinstance(inner, list) and len(inner) > 1 and isinstance(inner[1], str):
                    return inner[1] if _is_http_url(inner[1]) else None
    except (IndexError, ValueError, TypeError):
        return None
    return None


ClientFactory = Callable[[], httpx.AsyncClient]


def _default_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=_CALL_TIMEOUT, follow_redirects=True)


async def resolve_url(url: str, client_factory: ClientFactory | None = None) -> str:
    """The publisher URL for ``url``; ``url`` itself when it cannot be had."""
    article_id = google_news_id(url)
    if article_id is None:
        return url
    found, cached = _cache_get(url)
    if found:
        return cached or url
    decoded = decode_offline(article_id)
    if decoded:
        _cache_put(url, decoded)
        return decoded
    if not _http_enabled():
        return url
    resolved: str | None = None
    try:
        async with _get_semaphore():
            async with (client_factory or _default_client)() as client:
                resolved = await _resolve_online(client, article_id)
    except (httpx.HTTPError, asyncio.TimeoutError, OSError) as exc:
        logger.info("search_url_resolve_failed", error=type(exc).__name__)
        resolved = None
    _cache_put(url, resolved)
    return resolved or url


async def resolve_results(
    results: SearchResults,
    *,
    client_factory: ClientFactory | None = None,
    budget_s: float | None = None,
) -> SearchResults:
    """Replace Google News redirects in ``results`` with publisher URLs.

    Never raises and never waits past ``budget_s``: a link not resolved in
    time keeps its redirect (and finishes resolving into the cache for the
    next search).
    """
    if not _enabled():
        return results
    idx = [i for i, r in enumerate(results.results) if google_news_id(r.url)]
    if not idx:
        return results
    budget = _BUDGET_S if budget_s is None else budget_s
    tasks = {
        i: asyncio.ensure_future(resolve_url(results.results[i].url, client_factory))
        for i in idx
    }
    done, pending = await asyncio.wait(tasks.values(), timeout=budget)
    # Pending ones keep running into the cache (they're bounded by the
    # per-call timeout); nobody awaits them here.
    for t in pending:
        t.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)
    new = list(results.results)
    changed = 0
    for i, t in tasks.items():
        if t in done and not t.cancelled() and t.exception() is None:
            target = t.result()
            if target and target != new[i].url:
                new[i] = _with_url(new[i], target)
                changed += 1
    logger.info(
        "search_url_resolve",
        redirects=len(idx),
        resolved=changed,
        timed_out=len(pending),
    )
    if not changed:
        return results
    return replace(results, results=new)


def _with_url(r: SearchResult, url: str) -> SearchResult:
    return SearchResult(
        title=r.title,
        url=url,
        snippet=r.snippet,
        domain=domain_of(url),
        published_date=r.published_date,
    )
