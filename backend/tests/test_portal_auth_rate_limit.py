"""Phase 13 D — portal credential-route rate limiting.

Mirrors the admin login gate (``admin_session.py`` + ``LoginRateLimiter``)
on the portal's unauthenticated credential routes:

* ``POST /portal/auth/login``  — 5 failed / 15 min per email (cleared on
  success), 30 attempts / 15 min per IP.
* ``POST /portal/auth/signup`` — 5 failed / hour per email, 10 attempts /
  hour per IP (successes included: mass account farming is the vector).
* ``POST /portal/auth/password-reset/request`` — 3 / hour per email
  (pre-existing Commit 34 service-level limit, re-asserted here at the
  route so a refactor can't silently drop it).

Phase 12 A4 posture: these are credential routes, so a Redis outage must
throttle CONSERVATIVELY (bounded in-process window), never fail open.

Fails-before: pre-wiring, 12 consecutive wrong-password portal logins
returned [401] * 12 — never a 429 (captured before this commit). The
dependencies exercised below did not exist.

conftest note: the main app neutralises these limiters with a fresh-
fakeredis-per-request override so the suite's shared ``testclient`` IP
never accumulates counters. Each fixture here swaps in a PERSISTENT
limiter for the duration of the test, then restores the conftest
override.
"""

from __future__ import annotations

import uuid

import fakeredis
import pytest
from fastapi.testclient import TestClient

from app.api.routes import portal_auth
from app.main import app
from app.services.admin.login_service import LoginRateLimiter
from app.services.rate_limit_service import (
    _CONSERVATIVE_LIMIT,
    _conservative_hits,
)


PASSWORD = "Test1234!"
WRONG = "Wrong9999!"


class _RaisingRedis:
    """Stands in for a down Redis — every op raises (same as
    test_auth_limiter_outage)."""

    def get(self, *_a, **_k):
        raise ConnectionError("redis down")

    def incr(self, *_a, **_k):
        raise ConnectionError("redis down")

    def expire(self, *_a, **_k):
        raise ConnectionError("redis down")

    def ttl(self, *_a, **_k):
        raise ConnectionError("redis down")

    def delete(self, *_a, **_k):
        raise ConnectionError("redis down")


def _unique_email(tag: str) -> str:
    return f"{tag}-{uuid.uuid4().hex[:10]}@phase13d.test"


def _override(dep, provider):
    """Set a dependency override, returning a restore callable that
    reinstates whatever was there before (the conftest neutraliser)."""
    prev = app.dependency_overrides.get(dep)
    app.dependency_overrides[dep] = provider

    def _restore():
        if prev is not None:
            app.dependency_overrides[dep] = prev
        else:
            app.dependency_overrides.pop(dep, None)

    return _restore


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def login_limiter():
    """Persistent (fakeredis) production-shaped login limiter."""
    limiter = LoginRateLimiter(
        redis_client=fakeredis.FakeRedis(decode_responses=True),
        per_email_limit=5,
        per_email_window_seconds=15 * 60,
        per_ip_limit=30,
        per_ip_window_seconds=15 * 60,
        email_key_prefix="portal_login_attempts:",
        ip_key_prefix="portal_login_ip:",
    )
    restore = _override(
        portal_auth.get_portal_login_rate_limiter, lambda: limiter
    )
    yield limiter
    restore()


@pytest.fixture
def signup_limiter():
    """Persistent (fakeredis) production-shaped signup limiter."""
    limiter = LoginRateLimiter(
        redis_client=fakeredis.FakeRedis(decode_responses=True),
        per_email_limit=5,
        per_email_window_seconds=60 * 60,
        per_ip_limit=10,
        per_ip_window_seconds=60 * 60,
        email_key_prefix="portal_signup_attempts:",
        ip_key_prefix="portal_signup_ip:",
    )
    restore = _override(
        portal_auth.get_portal_signup_rate_limiter, lambda: limiter
    )
    yield limiter
    restore()


@pytest.fixture
def _clean_conservative_state():
    _conservative_hits.clear()
    yield
    _conservative_hits.clear()


def _signup(client, email, password=PASSWORD):
    return client.post(
        "/portal/auth/signup", json={"email": email, "password": password}
    )


def _login(client, email, password):
    return client.post(
        "/portal/auth/login", json={"email": email, "password": password}
    )


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


class TestLoginRateLimit:
    def test_sixth_failed_login_in_window_is_429(self, client, login_limiter):
        email = _unique_email("lock")
        assert _signup(client, email).status_code == 200
        for i in range(5):
            r = _login(client, email, WRONG)
            assert r.status_code == 401, f"attempt {i + 1}: {r.text}"
        r6 = _login(client, email, WRONG)
        assert r6.status_code == 429, (
            f"6th failed login in-window must 429 (got {r6.status_code})"
        )
        body = r6.json()
        assert body["error"]["code"] == "login_rate_limited"
        assert body["error"]["details"]["retry_after_seconds"] > 0

    def test_correct_password_also_blocked_once_limited(
        self, client, login_limiter
    ):
        """The check runs BEFORE auth — a locked window rejects even the
        right password (attacker can't confirm a guess mid-lockout)."""
        email = _unique_email("blocked")
        assert _signup(client, email).status_code == 200
        for _ in range(5):
            _login(client, email, WRONG)
        r = _login(client, email, PASSWORD)
        assert r.status_code == 429

    def test_successful_login_resets_email_counter(self, client, login_limiter):
        email = _unique_email("reset")
        assert _signup(client, email).status_code == 200
        for _ in range(4):
            assert _login(client, email, WRONG).status_code == 401
        ok = _login(client, email, PASSWORD)
        assert ok.status_code == 200, ok.text
        # Tokens still travel in Set-Cookie only — never the body.
        assert "access_token" not in ok.json()
        assert ok.cookies.get("portal_access_token")
        # Counter was cleared: 4 more failures are 401, not 429
        # (cumulatively 8 failures — without the reset, #6 onward
        # would have been 429).
        for i in range(4):
            r = _login(client, email, WRONG)
            assert r.status_code == 401, f"post-reset attempt {i + 1}: {r.status_code}"

    def test_per_email_counter_does_not_leak_across_accounts(
        self, client, login_limiter
    ):
        email_a = _unique_email("aaa")
        email_b = _unique_email("bbb")
        assert _signup(client, email_a).status_code == 200
        assert _signup(client, email_b).status_code == 200
        for _ in range(5):
            _login(client, email_a, WRONG)
        assert _login(client, email_a, WRONG).status_code == 429
        # Different account, same IP (30/15min — still has budget).
        assert _login(client, email_b, WRONG).status_code == 401


# ---------------------------------------------------------------------------
# Signup
# ---------------------------------------------------------------------------


class TestSignupRateLimit:
    def test_11th_signup_from_one_ip_in_window_is_429(
        self, client, signup_limiter
    ):
        for i in range(10):
            r = _signup(client, _unique_email(f"farm{i}"))
            assert r.status_code == 200, f"signup {i + 1}: {r.text}"
        r11 = _signup(client, _unique_email("farm-extra"))
        assert r11.status_code == 429, (
            f"11th signup/hour from one IP must 429 (got {r11.status_code})"
        )
        assert r11.json()["error"]["code"] == "signup_rate_limited"

    def test_duplicate_email_probing_is_throttled(self, client, signup_limiter):
        email = _unique_email("probe")
        assert _signup(client, email).status_code == 200
        for i in range(5):
            r = _signup(client, email)
            assert r.status_code == 409, f"probe {i + 1}: {r.status_code}"
        r = _signup(client, email)
        assert r.status_code == 429, (
            "6th failed signup for one email must 429 — duplicate-email "
            f"probing left unthrottled (got {r.status_code})"
        )


# ---------------------------------------------------------------------------
# Password reset — pre-existing Commit 34 per-email limit, asserted at the
# route so a refactor can't silently drop it.
# ---------------------------------------------------------------------------


class TestPasswordResetRateLimit:
    def test_fourth_reset_request_in_window_is_429(self, client):
        redis = fakeredis.FakeRedis(decode_responses=True)
        restore = _override(portal_auth._portal_redis_client, lambda: redis)
        try:
            email = _unique_email("pwreset")
            for i in range(3):
                r = client.post(
                    "/portal/auth/password-reset/request",
                    json={"email": email},
                )
                assert r.status_code == 200, f"request {i + 1}: {r.text}"
            r4 = client.post(
                "/portal/auth/password-reset/request", json={"email": email}
            )
            assert r4.status_code == 429
            assert r4.json()["error"]["code"] == "rate_limit_exceeded"
        finally:
            restore()


# ---------------------------------------------------------------------------
# Phase 12 A4 posture — Redis outage throttles conservatively, never
# fail-open. These are credential routes: the RATE_LIMIT_REDIS_OUTAGE_MODE
# knob must NOT apply.
# ---------------------------------------------------------------------------


class TestRedisOutageConservative:
    def test_login_outage_throttles_at_conservative_cap(
        self, client, _clean_conservative_state
    ):
        email = _unique_email("outage-login")
        assert _signup(client, email).status_code == 200  # limiter untouched
        limiter = LoginRateLimiter(
            redis_client=_RaisingRedis(),
            per_email_limit=5,
            per_email_window_seconds=15 * 60,
            per_ip_limit=30,
            per_ip_window_seconds=15 * 60,
            email_key_prefix="portal_login_attempts:",
            ip_key_prefix="portal_login_ip:",
        )
        restore = _override(
            portal_auth.get_portal_login_rate_limiter, lambda: limiter
        )
        try:
            for i in range(_CONSERVATIVE_LIMIT):
                r = _login(client, email, WRONG)
                assert r.status_code == 401, f"attempt {i + 1}: {r.status_code}"
            r = _login(client, email, WRONG)
            assert r.status_code == 429, (
                "Redis-down portal login must throttle conservatively, "
                f"not fail open (got {r.status_code})"
            )
        finally:
            restore()

    def test_signup_outage_throttles_at_conservative_cap(
        self, client, _clean_conservative_state
    ):
        limiter = LoginRateLimiter(
            redis_client=_RaisingRedis(),
            per_email_limit=5,
            per_email_window_seconds=60 * 60,
            per_ip_limit=10,
            per_ip_window_seconds=60 * 60,
            email_key_prefix="portal_signup_attempts:",
            ip_key_prefix="portal_signup_ip:",
        )
        restore = _override(
            portal_auth.get_portal_signup_rate_limiter, lambda: limiter
        )
        try:
            # Distinct emails → only the per-IP conservative window gates.
            for i in range(_CONSERVATIVE_LIMIT):
                r = _signup(client, _unique_email(f"outage-signup{i}"))
                assert r.status_code == 200, f"signup {i + 1}: {r.text}"
            r = _signup(client, _unique_email("outage-signup-extra"))
            assert r.status_code == 429, (
                "Redis-down portal signup must throttle conservatively, "
                f"not fail open (got {r.status_code})"
            )
        finally:
            restore()

    def test_password_reset_outage_throttles_at_3_per_hour(
        self, client, _clean_conservative_state
    ):
        restore = _override(
            portal_auth._portal_redis_client, lambda: _RaisingRedis()
        )
        try:
            email = _unique_email("outage-pwreset")
            for i in range(3):
                r = client.post(
                    "/portal/auth/password-reset/request",
                    json={"email": email},
                )
                assert r.status_code == 200, f"request {i + 1}: {r.text}"
            r4 = client.post(
                "/portal/auth/password-reset/request", json={"email": email}
            )
            assert r4.status_code == 429
        finally:
            restore()


# ---------------------------------------------------------------------------
# Production defaults pinned (mirror of test_login_rate_limit_config).
# ---------------------------------------------------------------------------


class TestProductionDefaultsPinned:
    def test_portal_login_limiter_defaults(self):
        portal_auth._portal_login_rate_limiter = None
        try:
            rl = portal_auth.get_portal_login_rate_limiter()
            assert rl._email_limit == 5
            assert rl._email_window == 15 * 60
            assert rl._ip_limit == 30
            assert rl._ip_window == 15 * 60
            assert rl._email_prefix == "portal_login_attempts:"
            assert rl._ip_prefix == "portal_login_ip:"
        finally:
            portal_auth._portal_login_rate_limiter = None

    def test_portal_signup_limiter_defaults(self):
        portal_auth._portal_signup_rate_limiter = None
        try:
            rl = portal_auth.get_portal_signup_rate_limiter()
            assert rl._email_limit == 5
            assert rl._email_window == 60 * 60
            assert rl._ip_limit == 10
            assert rl._ip_window == 60 * 60
            assert rl._email_prefix == "portal_signup_attempts:"
            assert rl._ip_prefix == "portal_signup_ip:"
        finally:
            portal_auth._portal_signup_rate_limiter = None

    def test_admin_prefixes_unchanged_by_default(self):
        """The prefix parametrisation must not move admin's Redis keys."""
        rl = LoginRateLimiter(
            redis_client=fakeredis.FakeRedis(decode_responses=True),
            per_email_limit=5,
            per_email_window_seconds=900,
            per_ip_limit=5,
            per_ip_window_seconds=900,
        )
        rl.record_failure(email="Admin@Example.com", ip="10.0.0.9")
        assert rl._redis.get("login_attempts:admin@example.com") == "1"
        assert rl._redis.get("admin_login_ip:10.0.0.9") == "1"
