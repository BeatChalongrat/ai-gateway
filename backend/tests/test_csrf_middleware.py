"""Phase 2B — Commit 10: CSRF double-submit middleware (TDD).

Pins the middleware contract from ``app.middleware.csrf.CsrfMiddleware``:

* Guards only /admin/* mutations (POST/PUT/DELETE/PATCH).
* GET/HEAD/OPTIONS are never checked.
* Non-admin paths (/v1/*, /auth/*, /portal/*, /health) never checked.
* csrf_token cookie AND X-CSRF-Token header must both be set and equal.
* Mismatch / missing → 403, error.code == "csrf_failure".
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.middleware import csrf as _csrf_mod
from app.middleware.csrf import CsrfMiddleware


@pytest.fixture(autouse=True)
def _restore_csrf_enforcement():
    """The shared conftest sets ``_BYPASS_FOR_TESTING=True`` for the
    integration TestClient. This test suite is the one place that
    actually needs the middleware to run — flip it back off for the
    duration of each test in this file.
    """
    prev = _csrf_mod._BYPASS_FOR_TESTING
    _csrf_mod._BYPASS_FOR_TESTING = False
    try:
        yield
    finally:
        _csrf_mod._BYPASS_FOR_TESTING = prev


@pytest.fixture
def app():
    app = FastAPI()
    app.add_middleware(CsrfMiddleware)

    @app.get("/admin/thing")
    def get_thing():
        return {"ok": True}

    @app.post("/admin/thing")
    def post_thing():
        return {"ok": True}

    @app.put("/admin/thing/{x}")
    def put_thing(x: str):
        return {"x": x}

    @app.delete("/admin/thing/{x}")
    def delete_thing(x: str):
        return {"deleted": x}

    @app.patch("/admin/thing/{x}")
    def patch_thing(x: str):
        return {"patched": x}

    @app.post("/v1/chat/completions")
    def public_post():
        return {"ok": True}

    @app.post("/auth/session/login")
    def session_login():
        return {"ok": True}

    return app


@pytest.fixture
def client(app):
    return TestClient(app)


class TestMutatingAdminPathsGuarded:
    def test_post_missing_both_returns_403(self, client):
        r = client.post("/admin/thing")
        assert r.status_code == 403
        assert r.json()["error"]["code"] == "csrf_failure"

    def test_post_missing_header_returns_403(self, client):
        r = client.post("/admin/thing", cookies={"csrf_token": "abc"})
        assert r.status_code == 403

    def test_post_missing_cookie_returns_403(self, client):
        r = client.post("/admin/thing", headers={"X-CSRF-Token": "abc"})
        assert r.status_code == 403

    def test_post_mismatched_returns_403(self, client):
        r = client.post(
            "/admin/thing",
            cookies={"csrf_token": "abc"},
            headers={"X-CSRF-Token": "xyz"},
        )
        assert r.status_code == 403

    def test_post_matching_passes(self, client):
        r = client.post(
            "/admin/thing",
            cookies={"csrf_token": "matching-token-1"},
            headers={"X-CSRF-Token": "matching-token-1"},
        )
        assert r.status_code == 200

    def test_put_guarded(self, client):
        r = client.put("/admin/thing/x1")
        assert r.status_code == 403
        r = client.put(
            "/admin/thing/x1",
            cookies={"csrf_token": "t"},
            headers={"X-CSRF-Token": "t"},
        )
        assert r.status_code == 200

    def test_delete_guarded(self, client):
        r = client.delete("/admin/thing/x1")
        assert r.status_code == 403
        r = client.delete(
            "/admin/thing/x1",
            cookies={"csrf_token": "t"},
            headers={"X-CSRF-Token": "t"},
        )
        assert r.status_code == 200

    def test_patch_guarded(self, client):
        r = client.patch("/admin/thing/x1", json={})
        assert r.status_code == 403
        r = client.patch(
            "/admin/thing/x1",
            json={},
            cookies={"csrf_token": "t"},
            headers={"X-CSRF-Token": "t"},
        )
        assert r.status_code == 200


class TestReadsAreNotGuarded:
    def test_get_admin_no_csrf_passes(self, client):
        r = client.get("/admin/thing")
        assert r.status_code == 200


class TestNonAdminPathsNotGuarded:
    def test_public_post_no_csrf_passes(self, client):
        r = client.post("/v1/chat/completions")
        assert r.status_code == 200

    def test_session_login_exempt(self, client):
        """Login sets the CSRF cookie on response; it can't require
        the cookie on the request. /auth/session/login is explicitly
        exempted so the login itself is not a chicken-and-egg."""
        r = client.post("/auth/session/login")
        assert r.status_code == 200


class TestConstantTimeCompare:
    """Documentary — the middleware uses hmac.compare_digest for the
    cookie/header equality check, which is constant-time regardless
    of which byte first differs. We can't really observe timing from
    a test, but we can at least confirm both representative cases
    return identically-shaped 403s so no side-channel is leaked via
    the response body.
    """

    def test_tiny_mismatch_same_status_and_message(self, client):
        r1 = client.post(
            "/admin/thing",
            cookies={"csrf_token": "abcdefgh"},
            headers={"X-CSRF-Token": "abcdefgi"},  # differs in last char
        )
        r2 = client.post(
            "/admin/thing",
            cookies={"csrf_token": "abcdefgh"},
            headers={"X-CSRF-Token": "zbcdefgh"},  # differs in first char
        )
        assert r1.status_code == r2.status_code == 403
        assert r1.json() == r2.json()
