"""Phase 2B — Commit 9: Bearer rejection on /admin/* (TDD).

P0-1 hidden-closure: Phase 2A Commit 6 removed the admin API key from
the browser bundle, but the backend ``require_admin`` dep still
accepted ``Authorization: Bearer <anything>`` — admin API keys leaked
before the cleanup would still work if an attacker held a copy.

This commit swaps admin routers onto ``require_admin_session`` which:

* Rejects any ``Authorization: Bearer`` header outright (401).
* Resolves identity from the ``admin_session`` cookie via
  SessionManager (HMAC-verified, Redis-backed, TTL-enforced).
* Enforces caller role >= PROJECT_ADMIN.

Tests drive the dep directly so the assertions stay fast and
deterministic (no need to boot the full admin router graph).
"""

from __future__ import annotations

from unittest.mock import patch

import fakeredis
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import require_admin_session
from app.services.admin.session_manager import SessionManager
from app.services.admin.session_service import get_session_manager


def _app_with_admin_endpoint(session_manager):
    from app.core.exceptions import GatewayError
    from app.middleware.error_handler import (
        handle_gateway_error,
        handle_unhandled_exception,
    )

    app = FastAPI()
    app.add_exception_handler(GatewayError, handle_gateway_error)
    app.add_exception_handler(Exception, handle_unhandled_exception)
    app.dependency_overrides[get_session_manager] = lambda: session_manager

    @app.get("/admin/ping")
    def ping(auth=pytest.importorskip("fastapi").Depends(require_admin_session)):
        return {"role": auth.role, "tenant_id": auth.tenant_id}

    return app


@pytest.fixture
def session_manager():
    return SessionManager(
        redis_client=fakeredis.FakeRedis(decode_responses=True),
        signing_secrets=["commit9-signing-secret-0123456789-abcdef"],
        idle_ttl_seconds=60,
        absolute_ttl_seconds=3600,
    )


@pytest.fixture
def client(session_manager):
    return TestClient(_app_with_admin_endpoint(session_manager))


class TestBearerRejected:
    def test_bearer_header_returns_401(self, client):
        r = client.get(
            "/admin/ping",
            headers={"Authorization": "Bearer gw_stale_admin_key_xyz"},
        )
        assert r.status_code == 401
        body = r.json()
        assert "Bearer tokens are not accepted" in str(body)

    def test_bearer_header_is_rejected_even_with_valid_cookie(
        self, client, session_manager
    ):
        """Defence in depth: if someone crafts a request with BOTH a
        valid session cookie AND a Bearer header, we still reject.
        The presence of Bearer is a misuse signal that we want to
        surface loudly.
        """
        cookie, _sid = session_manager.create_session(
            user_id="u-1", tenant_id="t-a", role="ADMIN"
        )
        r = client.get(
            "/admin/ping",
            headers={"Authorization": "Bearer leftover_key"},
            cookies={"admin_session": cookie},
        )
        assert r.status_code == 401

    def test_case_insensitive_bearer_match(self, client):
        r = client.get(
            "/admin/ping",
            headers={"Authorization": "bearer case-insensitive-attempt"},
        )
        assert r.status_code == 401

    def test_non_bearer_authorization_is_also_not_a_bypass(self, client):
        """``Authorization: Basic ...`` has always been unsupported on
        admin routes — verify it doesn't become a bypass now."""
        r = client.get(
            "/admin/ping",
            headers={"Authorization": "Basic dXNlcjpwYXNz"},
        )
        assert r.status_code == 401


class TestCookieAccepted:
    def test_valid_cookie_admin_returns_200(self, client, session_manager):
        cookie, _sid = session_manager.create_session(
            user_id="u-1", tenant_id="t-a", role="ADMIN"
        )
        r = client.get("/admin/ping", cookies={"admin_session": cookie})
        assert r.status_code == 200
        assert r.json()["role"] == "admin"

    def test_valid_cookie_viewer_role_rejected_403(
        self, client, session_manager
    ):
        """Cookie is valid but role < PROJECT_ADMIN → 403, not 401."""
        cookie, _sid = session_manager.create_session(
            user_id="u-2", tenant_id="t-a", role="VIEWER"
        )
        r = client.get("/admin/ping", cookies={"admin_session": cookie})
        assert r.status_code == 403

    def test_tenant_admin_role_accepted(self, client, session_manager):
        cookie, _sid = session_manager.create_session(
            user_id="u-3", tenant_id="t-a", role="TENANT_ADMIN"
        )
        r = client.get("/admin/ping", cookies={"admin_session": cookie})
        assert r.status_code == 200

    def test_missing_cookie_returns_401(self, client):
        r = client.get("/admin/ping")
        assert r.status_code == 401


class TestExposedApiKeyRotation:
    """Documentary test — asserts the current behaviour: any pre-Phase-2B
    admin API key is now useless against /admin/*. No rotation required
    for admin surface because the Bearer path is closed.
    """

    def test_gw_prefix_key_rejected(self, client):
        """The specific ``gw_*`` key format used before Phase 2A — if
        a copy leaked, it cannot be used against admin endpoints."""
        r = client.get(
            "/admin/ping",
            headers={"Authorization": "Bearer gw_666dff9bed57e5e41c6954e"},
        )
        assert r.status_code == 401
