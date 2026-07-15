"""Phase 12 A2 — SSRF guard on Replicate result-URL fetches.

The webhook route (``POST /webhooks/replicate``) and the job-loop poll
fallback both call ``fetch_result_bytes(url)`` with a URL taken from the
webhook BODY / poll response. Before the guard, any URL was fetched with
only a size cap — ``http://169.254.169.254/...`` (cloud metadata) or
``http://localhost:8080/...`` (internal services) included.

The guard lives INSIDE ``ReplicateAdapterBase.fetch_result_bytes`` (the
shared seam for both call sites): https-only + hostname allowlist
(``REPLICATE_DELIVERY_ALLOWED_HOSTS``, default ``replicate.delivery``,
exact or subdomain) + explicit IP-literal rejection. Rejection must
happen WITHOUT any HTTP request — proven here with a respx catch-all
route whose call_count stays 0.

Fails-before: revert the ``_validate_result_url(result_url)`` call in
``fetch_result_bytes`` and every rejection test below fails (the fetch
goes through and returns the mocked bytes).
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.core.config import settings
from app.services.media_gen.adapters.replicate_base import _validate_result_url
from app.services.media_gen.adapters.replicate_video import ReplicateVideoAdapter


@pytest.fixture(autouse=True)
def _default_allowlist(monkeypatch):
    monkeypatch.setattr(
        settings, "replicate_delivery_allowed_hosts", "replicate.delivery"
    )
    monkeypatch.setattr(settings, "replicate_request_timeout_sec", 30)


REJECTED_URLS = [
    # Cloud metadata endpoint (the canonical SSRF target).
    "http://169.254.169.254/latest/meta-data/iam/",
    # Internal service on the local host.
    "http://localhost:8080/x",
    # https but non-allowlisted external host.
    "https://evil.example/x",
    # https IP literals — loopback, private, link-local metadata.
    "https://127.0.0.1/x",
    "https://10.0.0.5/payload.mp4",
    "https://169.254.169.254/latest/meta-data/",
    # IPv6 loopback literal.
    "https://[::1]/x",
    # Suffix-spoofing: allowlisted name embedded in an attacker domain.
    "https://replicate.delivery.evil.com/x",
    "https://notreplicate.delivery.evil.com/x",
    # Scheme games.
    "file:///etc/passwd",
    "ftp://replicate.delivery/x",
    # Degenerate values.
    "",
    "https://",
]


class TestRejection:
    @pytest.mark.parametrize("url", REJECTED_URLS)
    @pytest.mark.asyncio
    @respx.mock
    async def test_rejected_without_any_http_request(self, url):
        # Catch-all spy: if the adapter opens ANY connection the route
        # records it (and would make the fetch "succeed").
        spy = respx.route().mock(
            return_value=httpx.Response(200, content=b"should-never-be-fetched")
        )
        with pytest.raises(ValueError):
            await ReplicateVideoAdapter().fetch_result_bytes(url)
        assert spy.call_count == 0, f"HTTP request escaped the guard for {url!r}"


class TestAllowed:
    @pytest.mark.asyncio
    @respx.mock
    async def test_apex_replicate_delivery_allowed(self):
        url = "https://replicate.delivery/pbxt/abc"
        respx.head(url).mock(
            return_value=httpx.Response(200, headers={"content-length": "4"})
        )
        respx.get(url).mock(return_value=httpx.Response(200, content=b"data"))
        assert await ReplicateVideoAdapter().fetch_result_bytes(url) == b"data"

    @pytest.mark.asyncio
    @respx.mock
    async def test_subdomain_replicate_delivery_allowed(self):
        url = "https://pbxt.replicate.delivery/out.mp4"
        respx.head(url).mock(
            return_value=httpx.Response(200, headers={"content-length": "3"})
        )
        respx.get(url).mock(return_value=httpx.Response(200, content=b"vid"))
        assert await ReplicateVideoAdapter().fetch_result_bytes(url) == b"vid"

    @pytest.mark.asyncio
    @respx.mock
    async def test_env_extension_of_allowlist(self, monkeypatch):
        monkeypatch.setattr(
            settings,
            "replicate_delivery_allowed_hosts",
            "replicate.delivery, cdn.example.org",
        )
        url = "https://cdn.example.org/artifact.bin"
        respx.head(url).mock(
            return_value=httpx.Response(200, headers={"content-length": "2"})
        )
        respx.get(url).mock(return_value=httpx.Response(200, content=b"ok"))
        assert await ReplicateVideoAdapter().fetch_result_bytes(url) == b"ok"


class TestValidatorUnit:
    def test_case_and_trailing_dot_normalised(self):
        # Should not raise: host normalisation covers FQDN dot + case.
        _validate_result_url("https://Replicate.DELIVERY./x")

    def test_userinfo_trick_rejected(self):
        # ``https://replicate.delivery@evil.example/`` — the real host is
        # evil.example; urlsplit's ``hostname`` sees through the userinfo.
        with pytest.raises(ValueError):
            _validate_result_url("https://replicate.delivery@evil.example/x")

    def test_port_is_irrelevant_host_still_checked(self):
        with pytest.raises(ValueError):
            _validate_result_url("https://internal-svc:8080/x")
