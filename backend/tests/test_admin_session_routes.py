"""Phase 2A — Commit 3: admin session login/logout/me + legacy 410 (TDD).

Pins the public contract for the new session-cookie admin auth.

Endpoints:
* POST /auth/session/login   — email + password → 200 + HttpOnly cookie
* POST /auth/session/logout  — clears cookie + revokes session
* GET  /auth/session/me      — current user from cookie
* POST /auth/login (LEGACY)  — returns 410 Gone, not 404

Rate limiting on login:
* 5 failed attempts per (email, 15 min) window → 429
* 20 login attempts per (ip, 1 min) window → 429

Security:
* Login always runs bcrypt even when the user does not exist
  (constant-ish time; no user-existence oracle)
* Cookie flags: HttpOnly, SameSite=Lax, Secure (SECURE is toggled off
  in tests / dev but on in production via cookie_secure setting)
* A non-HttpOnly ``csrf_token`` cookie is set alongside for double-
  submit CSRF verification (enforcement is Commit 5).
"""

from __future__ import annotations

import time
from typing import Any

import bcrypt
import fakeredis
import pytest
from fastapi.testclient import TestClient

from app.services.admin.login_service import LoginRateLimiter
from app.services.admin.session_manager import SessionManager


# ---------------------------------------------------------------------------
# Shared fixtures: a real admin user + isolated Redis
# ---------------------------------------------------------------------------


@pytest.fixture
def redis_client():
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def session_manager(redis_client):
    return SessionManager(
        redis_client=redis_client,
        signing_secrets=["test-signing-secret-abcdef0123456789"],
        idle_ttl_seconds=60,
        absolute_ttl_seconds=3600,
    )


@pytest.fixture
def rate_limiter(redis_client):
    return LoginRateLimiter(
        redis_client=redis_client,
        per_email_limit=5,
        per_email_window_seconds=900,
        per_ip_limit=20,
        per_ip_window_seconds=60,
    )


@pytest.fixture
def admin_user(db_session):
    """Create an ADMIN AdminUserORM with a known password."""
    import uuid

    from app.models.admin_user import AdminUserORM

    pw_hash = bcrypt.hashpw(b"CorrectHorseBattery9!", bcrypt.gensalt(rounds=4)).decode(
        "utf-8"
    )
    user = AdminUserORM(
        id=str(uuid.uuid4()),
        username="alice",
        email="alice@example.com",
        hashed_password=pw_hash,
        role="admin",
        is_active=True,
        org_id="tenant-a",
    )
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def client(sqlite_engine, session_manager, rate_limiter, admin_user):
    """Build a FastAPI app with session routes wired + DB override."""
    from fastapi import FastAPI
    from sqlalchemy.orm import sessionmaker

    from app.api.deps import get_db_session
    from app.api.routes.admin_session import router as session_router
    from app.api.routes.auth import router as legacy_auth_router
    from app.core.exceptions import GatewayError
    from app.middleware.error_handler import (
        handle_gateway_error,
        handle_unhandled_exception,
    )
    from app.services.admin.session_service import (
        get_login_rate_limiter,
        get_session_manager,
    )

    app = FastAPI()
    app.add_exception_handler(GatewayError, handle_gateway_error)
    app.add_exception_handler(Exception, handle_unhandled_exception)

    SessionLocal = sessionmaker(bind=sqlite_engine)

    def _db():
        s = SessionLocal()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    app.dependency_overrides[get_db_session] = _db
    app.dependency_overrides[get_session_manager] = lambda: session_manager
    app.dependency_overrides[get_login_rate_limiter] = lambda: rate_limiter
    app.include_router(session_router)
    app.include_router(legacy_auth_router)
    return TestClient(app)


# ---------------------------------------------------------------------------
# POST /auth/session/login
# ---------------------------------------------------------------------------


class TestSessionLogin:
    def test_valid_credentials_returns_200_and_sets_cookie(self, client):
        r = client.post(
            "/auth/session/login",
            json={"email": "alice@example.com", "password": "CorrectHorseBattery9!"},
        )
        assert r.status_code == 200, r.text

        body = r.json()
        assert body["user"]["email"] == "alice@example.com"
        assert body["user"]["role"] == "admin"

        # HttpOnly signed session cookie.
        cookies = r.cookies
        assert "admin_session" in cookies
        # A csrf_token cookie for double-submit CSRF.
        assert "csrf_token" in cookies

    def test_cookie_is_httponly_and_samesite_lax(self, client):
        r = client.post(
            "/auth/session/login",
            json={"email": "alice@example.com", "password": "CorrectHorseBattery9!"},
        )
        raw = r.headers.get_list("set-cookie")
        session_set = next(c for c in raw if c.lower().startswith("admin_session="))
        assert "httponly" in session_set.lower()
        assert "samesite=lax" in session_set.lower()

    def test_csrf_cookie_is_not_httponly(self, client):
        r = client.post(
            "/auth/session/login",
            json={"email": "alice@example.com", "password": "CorrectHorseBattery9!"},
        )
        raw = r.headers.get_list("set-cookie")
        csrf_set = next(c for c in raw if c.lower().startswith("csrf_token="))
        assert "httponly" not in csrf_set.lower()

    def test_wrong_password_returns_401(self, client):
        r = client.post(
            "/auth/session/login",
            json={"email": "alice@example.com", "password": "wrong"},
        )
        assert r.status_code == 401

    def test_unknown_user_returns_401_not_404(self, client):
        """User-existence must not leak through the status code."""
        r = client.post(
            "/auth/session/login",
            json={"email": "ghost@example.com", "password": "whatever"},
        )
        assert r.status_code == 401

    def test_unknown_user_runs_bcrypt_to_avoid_timing_oracle(self, client):
        """A miss must still run bcrypt so timing doesn't reveal user existence.

        We can't assert wall-clock equality reliably, but we can assert
        the miss path takes at least a non-trivial minimum (bcrypt's floor
        is ~tens of ms even at cost=4 used in tests).
        """
        t0 = time.perf_counter()
        client.post(
            "/auth/session/login",
            json={"email": "ghost@example.com", "password": "whatever"},
        )
        miss = time.perf_counter() - t0
        # bcrypt cost=4 ~ 2ms, so assert at least 1ms. Threshold is
        # intentionally tiny — we only want to assert "we didn't skip".
        assert miss > 0.001

    def test_disabled_user_returns_401(self, client, db_session, admin_user):
        admin_user.is_active = False
        db_session.commit()
        r = client.post(
            "/auth/session/login",
            json={"email": "alice@example.com", "password": "CorrectHorseBattery9!"},
        )
        assert r.status_code == 401

    def test_missing_fields_returns_422(self, client):
        r = client.post("/auth/session/login", json={"email": "alice@example.com"})
        assert r.status_code == 422

    def test_malformed_email_returns_422(self, client):
        r = client.post(
            "/auth/session/login",
            json={"email": "not-an-email", "password": "x"},
        )
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


class TestLoginRateLimit:
    def test_repeated_failures_eventually_429(self, client):
        # 5 failures per email window → on the 6th we should be blocked.
        for _ in range(5):
            r = client.post(
                "/auth/session/login",
                json={"email": "alice@example.com", "password": "wrong"},
            )
            assert r.status_code == 401

        r = client.post(
            "/auth/session/login",
            json={"email": "alice@example.com", "password": "wrong"},
        )
        assert r.status_code == 429, r.text

    def test_rate_limit_also_blocks_correct_password_while_window_active(self, client):
        """A locked-out email can't log in even with the right password.

        Matches the industry-standard brute-force-defence behaviour.
        Unlock is by waiting the window, not by submitting correct creds.
        """
        for _ in range(5):
            client.post(
                "/auth/session/login",
                json={"email": "alice@example.com", "password": "wrong"},
            )
        r = client.post(
            "/auth/session/login",
            json={"email": "alice@example.com", "password": "CorrectHorseBattery9!"},
        )
        assert r.status_code == 429

    def test_successful_login_clears_failure_counter(self, client):
        """A successful login resets the per-email counter so future
        typos don't cumulatively lock the user out.
        """
        # 3 typos
        for _ in range(3):
            client.post(
                "/auth/session/login",
                json={"email": "alice@example.com", "password": "wrong"},
            )
        # Correct login → counter reset
        r = client.post(
            "/auth/session/login",
            json={"email": "alice@example.com", "password": "CorrectHorseBattery9!"},
        )
        assert r.status_code == 200
        # Now 2 more typos (would have been #4 and #5 cumulatively)
        for _ in range(2):
            r = client.post(
                "/auth/session/login",
                json={"email": "alice@example.com", "password": "wrong"},
            )
            assert r.status_code == 401  # still 401, not yet rate-limited


# ---------------------------------------------------------------------------
# GET /auth/session/me
# ---------------------------------------------------------------------------


class TestSessionMe:
    def _login(self, client):
        r = client.post(
            "/auth/session/login",
            json={"email": "alice@example.com", "password": "CorrectHorseBattery9!"},
        )
        assert r.status_code == 200
        return r

    def test_me_with_valid_cookie_returns_user(self, client):
        self._login(client)
        r = client.get("/auth/session/me")
        assert r.status_code == 200
        assert r.json()["user"]["email"] == "alice@example.com"

    def test_me_without_cookie_returns_401(self, client):
        r = client.get("/auth/session/me")
        assert r.status_code == 401

    def test_me_with_tampered_cookie_returns_401(self, client):
        self._login(client)
        # Tamper with the stored cookie.
        jar = client.cookies
        raw = jar.get("admin_session")
        jar.set("admin_session", raw[:-1] + ("A" if raw[-1] != "A" else "B"))
        r = client.get("/auth/session/me")
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# POST /auth/session/logout
# ---------------------------------------------------------------------------


class TestSessionLogout:
    def _login(self, client):
        r = client.post(
            "/auth/session/login",
            json={"email": "alice@example.com", "password": "CorrectHorseBattery9!"},
        )
        assert r.status_code == 200

    def test_logout_clears_cookie_and_invalidates_session(self, client):
        self._login(client)
        r = client.post("/auth/session/logout")
        assert r.status_code == 200

        # Cookie should now return empty on subsequent /me.
        me = client.get("/auth/session/me")
        assert me.status_code == 401

    def test_logout_without_cookie_is_idempotent(self, client):
        """Users can safely log out twice without a 401."""
        r = client.post("/auth/session/logout")
        assert r.status_code == 200

    def test_logout_tampered_cookie_still_clears(self, client):
        self._login(client)
        jar = client.cookies
        jar.set("admin_session", "garbage")
        r = client.post("/auth/session/logout")
        # Clearing is best-effort — must not 500.
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# Legacy /auth/login → 410 Gone
# ---------------------------------------------------------------------------


class TestLegacyLoginGone:
    def test_legacy_auth_login_returns_410(self, client):
        r = client.post(
            "/auth/login",
            json={"username": "alice", "password": "whatever"},
        )
        assert r.status_code == 410, r.text

    def test_410_body_points_at_new_endpoint(self, client):
        r = client.post(
            "/auth/login",
            json={"username": "alice", "password": "whatever"},
        )
        body = r.json()
        # Must mention the replacement so clients know what to call.
        assert "/auth/session/login" in (str(body) or "")
