"""Regression test for the audit-seed portal users.

Guards against two classes of bug we've hit before:

1. Beat reported `alice@acme.test` couldn't log in while `bob@globex.test`
   could, even though both were seeded the same way. The backend /
   portal proxy both accepted alice fine — the eventual theory was a
   stale `.next` cache on the portal side and drifted password_hash
   from an earlier seed pass. This test locks the contract: every
   row in ``PORTAL_USERS`` has a bcrypt hash that verifies against
   the documented password.

2. The seed used to be "create-only" — re-running it wouldn't fix a
   drifted row. Now it re-applies the spec in-place. The idempotency
   test below confirms that after a hand-edit, re-seeding restores
   the documented state.
"""

from __future__ import annotations

import bcrypt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.models.portal_user import PortalUserORM
from scripts.seed_audit_data import PORTAL_USERS, seed_portal_users

from tests.conftest import _integration_engine


_Session = sessionmaker(bind=_integration_engine)


@pytest.fixture
def _seeded_portal_users():
    """Run seed_portal_users against the shared in-memory DB.

    Cleans any existing portal rows first so other tests' leftovers
    don't poison the seed run.
    """
    db = _Session()
    try:
        db.query(PortalUserORM).delete()
        db.commit()
        seed_portal_users(db)
        db.commit()
        yield
    finally:
        # Clean up so later tests see a blank portal_users table.
        db.query(PortalUserORM).delete()
        db.commit()
        db.close()


@pytest.fixture
def client():
    return TestClient(app)


# ---------------------------------------------------------------------------
# Password-hash sanity: what we wrote into the DB actually verifies.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spec", PORTAL_USERS, ids=lambda s: s["email"])
def test_seeded_hash_verifies_against_spec_password(_seeded_portal_users, spec):
    """Each seeded row's bcrypt hash accepts the documented password.

    If this ever fails, `make seed` is producing a row that can't
    log in — which is exactly what Beat hit in live testing.
    """
    db = _Session()
    try:
        row = db.query(PortalUserORM).filter_by(email=spec["email"]).one_or_none()
        assert row is not None, f"{spec['email']} was not seeded"
        assert bcrypt.checkpw(
            spec["password"].encode("utf-8"),
            row.password_hash.encode("utf-8"),
        ), f"{spec['email']} password_hash does not match PORTAL_USERS password"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# End-to-end: the login endpoint behaves as expected for every spec.
# ---------------------------------------------------------------------------


def _login(client, email, password):
    return client.post(
        "/portal/auth/login",
        json={"email": email, "password": password},
    )


def test_active_verified_users_can_log_in(_seeded_portal_users, client):
    """alice / bob / charlie — active + email_verified → 200 + tokens."""
    for email, password in [
        ("alice@acme.test", "Alice!2345"),
        ("bob@globex.test", "Bob!2345"),
        ("charlie@acme.test", "Charlie!2345"),
    ]:
        r = _login(client, email, password)
        assert r.status_code == 200, f"{email}: {r.status_code} {r.text}"
        body = r.json()
        assert body["user"]["email"] == email
        assert body["user"]["status"] == "active"
        assert body["user"]["email_verified"] is True
        assert body["access_token"]
        assert body["refresh_token"]


def test_suspended_user_is_rejected_distinctly(_seeded_portal_users, client):
    """dave is suspended — error should be "Account is not active",
    not the generic "Invalid email or password" (we want to be able
    to tell the two apart in support triage)."""
    r = _login(client, "dave@acme.test", "Dave!2345")
    assert r.status_code == 401
    # auth_service.login raises AuthenticationError with a specific
    # message — make sure we're on the "account" branch, not the
    # "credentials" branch.
    assert "not active" in r.json()["error"]["message"].lower()


def test_unverified_user_cannot_log_in(_seeded_portal_users, client):
    """eve has email_verified=False — should be blocked with the
    dedicated ``email_not_verified`` error code so the UI can route
    her into the "resend verification" flow rather than showing
    "wrong password"."""
    r = _login(client, "eve@globex.test", "Eve!2345")
    assert r.status_code == 403
    body = r.json()
    assert body["error"]["code"] == "email_not_verified"


def test_wrong_password_returns_generic_error(_seeded_portal_users, client):
    """Sanity — if you enter the wrong password for a real seeded
    user, the error message is the generic one (no user-existence
    leak)."""
    r = _login(client, "alice@acme.test", "not-the-real-password")
    assert r.status_code == 401
    assert "invalid email or password" in r.json()["error"]["message"].lower()


# ---------------------------------------------------------------------------
# Idempotency: the seed-on-existing path restores the documented spec.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# tenant_role assignment — regression for the "portal-UX" overhaul.
# Prior to that change the seed left every user at the ORM default
# ``member`` role, which meant the Team page had no Owner — every
# tenant loaded into the portal with the Invite button hidden.
# ---------------------------------------------------------------------------


def test_seed_assigns_owners_per_tenant(_seeded_portal_users):
    """Alice owns Acme, Bob owns Globex; everyone else stays member.

    Keeps the Team page demo honest: the "crown + Owner" badge the UI
    renders is data-driven, so the seed has to keep at least one owner
    per tenant.
    """
    db = _Session()
    try:
        owners_by_email = {
            row.email: row.tenant_role
            for row in db.query(PortalUserORM).all()
        }
        assert owners_by_email["alice@acme.test"] == "owner"
        assert owners_by_email["bob@globex.test"] == "owner"
        assert owners_by_email["charlie@acme.test"] == "member"
        assert owners_by_email["dave@acme.test"] == "member"
        assert owners_by_email["eve@globex.test"] == "member"
    finally:
        db.close()


def test_reseed_restores_owner_role_after_drift():
    """Regression: re-seed must restore tenant_role, not just passwords.

    If an operator demotes Alice to member manually (e.g. testing an
    RBAC flow), re-running `make seed` must put her back as owner —
    otherwise the post-seed DB has no owner for Acme and every Acme
    user's Team page is stuck without invite capability.
    """
    db = _Session()
    try:
        db.query(PortalUserORM).delete()
        db.commit()

        seed_portal_users(db)
        db.commit()

        alice = db.query(PortalUserORM).filter_by(email="alice@acme.test").one()
        alice.tenant_role = "member"
        db.commit()

        seed_portal_users(db)
        db.commit()
        db.refresh(alice)

        assert alice.tenant_role == "owner"
    finally:
        db.query(PortalUserORM).delete()
        db.commit()
        db.close()


def test_seeded_display_names_are_production_safe(_seeded_portal_users):
    """No decorative emoji / Thai test strings in the shipped seed.

    The audit seed used to carry "Charlie Chen 🐉 ทดสอบ" so we could
    smoke-test Unicode rendering; that leaked into PR screenshots of
    the Team page and made the portal look unfinished. Unicode support
    is covered separately in ``test_portal_auth`` — the shipped seed
    must read as realistic production data.
    """
    db = _Session()
    try:
        for row in db.query(PortalUserORM).all():
            name = row.display_name or ""
            # No emoji in the Basic Multilingual Plane overflow range.
            assert all(ord(c) < 0x1F000 for c in name), f"emoji in {name}"
            # No Thai-script markers.
            assert all(not (0x0E00 <= ord(c) <= 0x0E7F) for c in name), (
                f"Thai script in {name}"
            )
    finally:
        db.close()


def test_reseeding_overwrites_drifted_password_hash():
    """Regression for the create-only seed bug.

    Simulates the "someone ran seed, then the row got into a weird
    state, then they re-ran seed hoping to fix it" path. The old
    behavior was that re-seed skipped existing users — so the bad
    hash survived. Now re-seed re-hashes and restores the spec.
    """
    db = _Session()
    try:
        db.query(PortalUserORM).delete()
        db.commit()

        # First seed.
        seed_portal_users(db)
        db.commit()

        # Break alice's hash on purpose.
        alice = db.query(PortalUserORM).filter_by(email="alice@acme.test").one()
        alice.password_hash = bcrypt.hashpw(
            b"definitely-not-the-seed-password", bcrypt.gensalt(rounds=4)
        ).decode("utf-8")
        alice.status = "suspended"
        alice.email_verified = False
        db.commit()

        # Confirm drift — old password rejects.
        assert not bcrypt.checkpw(
            b"Alice!2345", alice.password_hash.encode("utf-8")
        )

        # Second seed — should restore the spec in place.
        seed_portal_users(db)
        db.commit()
        db.refresh(alice)

        assert bcrypt.checkpw(
            b"Alice!2345", alice.password_hash.encode("utf-8")
        ), "re-seed did not restore alice's password hash"
        assert alice.status == "active"
        assert alice.email_verified is True
    finally:
        db.query(PortalUserORM).delete()
        db.commit()
        db.close()
