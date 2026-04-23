"""Phase 2A — Commit 1: SessionManager tests (TDD).

Pins the contract for the admin session store:

* Sessions are identified by a random UUID. The cookie value is
  ``itsdangerous``-signed so Redis never sees unsigned IDs.
* Session state lives in Redis under ``session:<uuid>``; a per-user
  set lives under ``session:user:<user_id>`` so revoke-all is O(log n).
* ``create_session`` returns (signed_cookie_value, session_id). The
  cookie value is what the browser stores; the session_id is the
  internal UUID.
* ``verify_cookie`` rejects tampered or expired cookies; valid
  cookies return the session payload.
* Sliding TTL: every ``verify_cookie`` success bumps the idle TTL.
* Absolute TTL: ``created_at`` is immutable; once 7 days elapse,
  verify fails even if the sliding window would still be alive.
* Revocation: ``revoke_session`` and ``revoke_all_for_user`` delete
  Redis state so any future verify fails.
* Key rotation: cookies signed by any secret in
  ``SESSION_SIGNING_SECRETS`` verify; new sessions always sign with
  the first (newest) secret.

These tests drive the public surface of
``app.services.admin.session_manager.SessionManager``; no module is
imported from the implementation beyond what the tests pin.
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import patch

import fakeredis
import pytest
from itsdangerous import URLSafeSerializer

from app.services.admin.session_manager import (
    SessionExpired,
    SessionInvalid,
    SessionManager,
    SessionRevoked,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def redis_client():
    """In-process Redis shim. Each test gets a fresh key space."""
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def manager(redis_client):
    return SessionManager(
        redis_client=redis_client,
        signing_secrets=["primary-secret-abcdef"],
        idle_ttl_seconds=60,
        absolute_ttl_seconds=3600,
    )


# ---------------------------------------------------------------------------
# create_session
# ---------------------------------------------------------------------------


class TestCreateSession:
    def test_returns_signed_cookie_and_session_id(self, manager):
        cookie, sid = manager.create_session(
            user_id="admin-1",
            tenant_id="tenant-a",
            role="ADMIN",
            ip="10.0.0.1",
            user_agent="pytest",
        )
        assert isinstance(cookie, str) and cookie
        assert isinstance(sid, str) and len(sid) >= 32  # uuid4 hex

    def test_cookie_is_signed_not_plaintext_sid(self, manager):
        cookie, sid = manager.create_session(user_id="u1", tenant_id="t1", role="ADMIN")
        # The bare session id must NOT be the cookie value (must be signed).
        assert sid not in cookie or cookie != sid

    def test_session_payload_persisted_in_redis(self, manager, redis_client):
        _cookie, sid = manager.create_session(
            user_id="u1", tenant_id="t1", role="ADMIN", ip="1.2.3.4"
        )
        assert redis_client.exists(f"session:{sid}") == 1

    def test_user_index_updated(self, manager, redis_client):
        _cookie, sid = manager.create_session(user_id="u1", tenant_id="t1", role="ADMIN")
        members = redis_client.smembers("session:user:u1")
        assert sid in members

    def test_multiple_sessions_per_user_tracked(self, manager, redis_client):
        manager.create_session(user_id="u1", tenant_id="t1", role="ADMIN")
        manager.create_session(user_id="u1", tenant_id="t1", role="ADMIN")
        assert len(redis_client.smembers("session:user:u1")) == 2


# ---------------------------------------------------------------------------
# verify_cookie
# ---------------------------------------------------------------------------


class TestVerifyCookie:
    def test_valid_cookie_returns_payload(self, manager):
        cookie, sid = manager.create_session(
            user_id="u1", tenant_id="t1", role="TENANT_ADMIN", ip="9.9.9.9"
        )
        payload = manager.verify_cookie(cookie)
        assert payload["session_id"] == sid
        assert payload["user_id"] == "u1"
        assert payload["tenant_id"] == "t1"
        assert payload["role"] == "TENANT_ADMIN"

    def test_tampered_cookie_rejected(self, manager):
        cookie, _sid = manager.create_session(
            user_id="u1", tenant_id="t1", role="ADMIN"
        )
        tampered = cookie[:-1] + ("A" if cookie[-1] != "A" else "B")
        with pytest.raises(SessionInvalid):
            manager.verify_cookie(tampered)

    def test_garbage_cookie_rejected(self, manager):
        with pytest.raises(SessionInvalid):
            manager.verify_cookie("not-a-real-cookie")

    def test_cookie_signed_with_wrong_secret_rejected(self, redis_client, manager):
        # Build a cookie signed with a secret the manager does NOT accept.
        evil = URLSafeSerializer("evil-secret", salt="admin-session")
        forged = evil.dumps("fake-session-id")
        with pytest.raises(SessionInvalid):
            manager.verify_cookie(forged)

    def test_cookie_valid_signature_but_missing_redis_row_rejected(
        self, manager, redis_client
    ):
        cookie, sid = manager.create_session(
            user_id="u1", tenant_id="t1", role="ADMIN"
        )
        # Manually evict the Redis row; the signature is still fine.
        redis_client.delete(f"session:{sid}")
        with pytest.raises(SessionRevoked):
            manager.verify_cookie(cookie)


# ---------------------------------------------------------------------------
# Sliding TTL (refresh on verify)
# ---------------------------------------------------------------------------


class TestSlidingTtl:
    def test_idle_ttl_applied_on_create(self, manager, redis_client):
        _cookie, sid = manager.create_session(
            user_id="u1", tenant_id="t1", role="ADMIN"
        )
        ttl = redis_client.ttl(f"session:{sid}")
        # idle_ttl_seconds=60 in fixture; allow 1s drift for test execution.
        assert 58 <= ttl <= 60

    def test_verify_refreshes_idle_ttl(self, manager, redis_client):
        cookie, sid = manager.create_session(
            user_id="u1", tenant_id="t1", role="ADMIN"
        )
        # Simulate 30 seconds elapsed on the key.
        redis_client.expire(f"session:{sid}", 30)
        manager.verify_cookie(cookie)
        ttl = redis_client.ttl(f"session:{sid}")
        assert ttl >= 58, f"verify_cookie should have reset TTL to 60, got {ttl}"


# ---------------------------------------------------------------------------
# Absolute TTL (hard cap)
# ---------------------------------------------------------------------------


class TestAbsoluteTtl:
    def test_absolute_cap_rejected_even_if_sliding_window_alive(
        self, manager, redis_client
    ):
        """Stored row is alive (TTL not hit) but created_at is old.

        This proves the absolute cap is enforced from the ``created_at``
        field in the payload, not merely from Redis' own TTL — which
        matters because the sliding refresh keeps bumping the TTL.
        """
        cookie, sid = manager.create_session(
            user_id="u1", tenant_id="t1", role="ADMIN"
        )
        # Rewrite the stored payload to look like it was created longer
        # ago than the absolute cap (3600s in the fixture).
        import json as _json
        raw = redis_client.get(f"session:{sid}")
        data = _json.loads(raw)
        data["created_at"] = int(time.time()) - 3700
        redis_client.set(f"session:{sid}", _json.dumps(data), ex=60)

        with pytest.raises(SessionExpired):
            manager.verify_cookie(cookie)

        # And the expired session is scrubbed on the way out.
        assert redis_client.exists(f"session:{sid}") == 0

    def test_within_absolute_cap_ok(self, manager):
        cookie, _sid = manager.create_session(
            user_id="u1", tenant_id="t1", role="ADMIN"
        )
        payload = manager.verify_cookie(cookie)
        assert payload["user_id"] == "u1"


# ---------------------------------------------------------------------------
# Revocation
# ---------------------------------------------------------------------------


class TestRevocation:
    def test_revoke_session(self, manager, redis_client):
        cookie, sid = manager.create_session(
            user_id="u1", tenant_id="t1", role="ADMIN"
        )
        manager.revoke_session(sid)
        assert redis_client.exists(f"session:{sid}") == 0
        # The user-index set should no longer reference this sid.
        assert sid not in redis_client.smembers("session:user:u1")
        with pytest.raises(SessionRevoked):
            manager.verify_cookie(cookie)

    def test_revoke_all_for_user(self, manager, redis_client):
        c1, s1 = manager.create_session(user_id="u1", tenant_id="t1", role="ADMIN")
        c2, s2 = manager.create_session(user_id="u1", tenant_id="t1", role="ADMIN")
        c_other, _s_other = manager.create_session(
            user_id="u2", tenant_id="t1", role="ADMIN"
        )

        removed = manager.revoke_all_for_user("u1")
        assert removed == 2
        assert redis_client.exists(f"session:{s1}") == 0
        assert redis_client.exists(f"session:{s2}") == 0
        # Unrelated user's session survived.
        payload = manager.verify_cookie(c_other)
        assert payload["user_id"] == "u2"
        with pytest.raises(SessionRevoked):
            manager.verify_cookie(c1)
        with pytest.raises(SessionRevoked):
            manager.verify_cookie(c2)

    def test_revoke_nonexistent_session_is_noop(self, manager):
        # Must not raise — idempotent logout is required.
        manager.revoke_session("no-such-session-id")

    def test_revoke_all_for_user_with_no_sessions_returns_zero(self, manager):
        assert manager.revoke_all_for_user("ghost") == 0


# ---------------------------------------------------------------------------
# Key rotation
# ---------------------------------------------------------------------------


class TestKeyRotation:
    def test_cookie_signed_by_older_secret_still_verifies(self, redis_client):
        # Start with a single-secret manager, create a session.
        old_mgr = SessionManager(
            redis_client=redis_client,
            signing_secrets=["old-secret"],
            idle_ttl_seconds=60,
            absolute_ttl_seconds=3600,
        )
        cookie, _sid = old_mgr.create_session(
            user_id="u1", tenant_id="t1", role="ADMIN"
        )

        # Rotate: new secret first, old secret retained.
        rotated_mgr = SessionManager(
            redis_client=redis_client,
            signing_secrets=["new-secret", "old-secret"],
            idle_ttl_seconds=60,
            absolute_ttl_seconds=3600,
        )
        payload = rotated_mgr.verify_cookie(cookie)
        assert payload["user_id"] == "u1"

    def test_new_cookies_signed_with_first_secret(self, redis_client):
        mgr = SessionManager(
            redis_client=redis_client,
            signing_secrets=["new-secret", "old-secret"],
            idle_ttl_seconds=60,
            absolute_ttl_seconds=3600,
        )
        cookie, _sid = mgr.create_session(
            user_id="u1", tenant_id="t1", role="ADMIN"
        )
        # A manager with ONLY the new secret must be able to verify it —
        # proving the new secret signed it.
        new_only = SessionManager(
            redis_client=redis_client,
            signing_secrets=["new-secret"],
            idle_ttl_seconds=60,
            absolute_ttl_seconds=3600,
        )
        payload = new_only.verify_cookie(cookie)
        assert payload["user_id"] == "u1"

    def test_rejected_after_old_secret_dropped(self, redis_client):
        old_mgr = SessionManager(
            redis_client=redis_client,
            signing_secrets=["old-secret"],
            idle_ttl_seconds=60,
            absolute_ttl_seconds=3600,
        )
        cookie, _sid = old_mgr.create_session(
            user_id="u1", tenant_id="t1", role="ADMIN"
        )
        new_only = SessionManager(
            redis_client=redis_client,
            signing_secrets=["new-secret"],
            idle_ttl_seconds=60,
            absolute_ttl_seconds=3600,
        )
        with pytest.raises(SessionInvalid):
            new_only.verify_cookie(cookie)


# ---------------------------------------------------------------------------
# Construction safety
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_empty_signing_secrets_rejected(self, redis_client):
        with pytest.raises(ValueError):
            SessionManager(
                redis_client=redis_client,
                signing_secrets=[],
                idle_ttl_seconds=60,
                absolute_ttl_seconds=3600,
            )

    def test_idle_ttl_must_not_exceed_absolute(self, redis_client):
        with pytest.raises(ValueError):
            SessionManager(
                redis_client=redis_client,
                signing_secrets=["s"],
                idle_ttl_seconds=10,
                absolute_ttl_seconds=5,
            )
