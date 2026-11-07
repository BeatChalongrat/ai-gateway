"""FIX88 acceptance — the backfill IS today.

Using ONLY pre-FIX88 read APIs (``GET /portal/me`` fields, the admin
list / detail rows, the ``credit_percent_used`` number), a user whose
ceiling was backfilled to ``balance + used`` (what migration 088 does)
reads exactly what the old derivation produced, and a gateway
deduction keeps the ceiling where it is and the ring identical to the
old derivation.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_portal_user
from app.main import app
from app.services.gateway.portal_credit_check import deduct_portal_credit
from app.services.portal import credit_service

from tests.fix88_support import (
    OWNER_BALANCE,
    OWNER_QUOTA_BACKFILLED,
    OWNER_USED,
    Session,
    clean_portal_tables,
    make_key,
    make_user,
    reload,
)


def _old_percent(balance: int, used: int) -> float:
    """The pre-FIX88 expression from ``portal_me.get_me``, verbatim:
    lifetime = balance + used; percent = used / lifetime * 100."""
    lifetime = balance + used
    percent_used = (used / lifetime * 100.0) if lifetime > 0 else 0.0
    return round(percent_used, 2)


@pytest.fixture(autouse=True)
def _clean():
    db = Session()
    try:
        clean_portal_tables(db)
        yield
    finally:
        db.close()
        app.dependency_overrides.pop(get_portal_user, None)


@pytest.fixture
def client():
    return TestClient(app)


def _login_as(user_id: str):
    user = reload(user_id)
    app.dependency_overrides[get_portal_user] = lambda: user
    return user


def test_owner_numbers_backfilled_read_as_yesterday(client):
    db = Session()
    try:
        u = make_user(db, balance=OWNER_BALANCE, used=OWNER_USED)
    finally:
        db.close()
    assert OWNER_QUOTA_BACKFILLED == 100_150_000
    assert reload(u.id).credit_quota == OWNER_QUOTA_BACKFILLED

    _login_as(u.id)
    me = client.get("/portal/me").json()
    assert me["credit_balance"] == OWNER_BALANCE
    assert me["credit_used"] == OWNER_USED
    assert me["credit_percent_used"] == _old_percent(OWNER_BALANCE, OWNER_USED)
    assert me["credit_percent_used"] == 47.84

    # Admin list + detail: the same numbers.
    row = client.get(f"/admin/portal-users/{u.id}").json()
    assert row["credit_balance"] == OWNER_BALANCE
    assert row["credit_used"] == OWNER_USED
    listed = [
        r for r in client.get("/admin/portal-users").json()["users"] if r["id"] == u.id
    ][0]
    assert listed["credit_balance"] == OWNER_BALANCE
    assert listed["credit_used"] == OWNER_USED


@pytest.mark.parametrize(
    "balance,used",
    [
        (OWNER_BALANCE, OWNER_USED),
        (0, 0),  # never granted anything: quota 0 → old formula, 0.0
        (100_000, 0),  # fresh signup, nothing used
        (0, 100_000),  # drained
        (1, 2),
    ],
)
def test_ring_equals_old_derivation_for_every_backfilled_shape(client, balance, used):
    db = Session()
    try:
        u = make_user(db, balance=balance, used=used)
    finally:
        db.close()
    _login_as(u.id)
    me = client.get("/portal/me").json()
    assert me["credit_percent_used"] == _old_percent(balance, used)


def test_gateway_deduction_keeps_quota_and_ring_matches_old_derivation(client):
    db = Session()
    try:
        u = make_user(db, balance=OWNER_BALANCE, used=OWNER_USED)
        uid = u.id
        make_key(db, u.id, key_id="fix88-deduct-key")
        # Cost-based deduction through the gateway hook (sync path).
        deduct_portal_credit(
            db, "fix88-deduct-key", "req-fix88", cost_usd=0.0000833, tokens_used=250
        )
        db.commit()
        # And the service function the async hook mirrors.
        credit_service.deduct_credits(db, uid, 1_000, request_id="req-fix88-2")
        db.commit()
    finally:
        db.close()

    after = reload(uid)
    assert after.credit_quota == OWNER_QUOTA_BACKFILLED  # untouched
    assert after.credit_balance + after.credit_used == OWNER_QUOTA_BACKFILLED
    assert after.credit_used == OWNER_USED + 250 + 1_000

    _login_as(uid)
    me = client.get("/portal/me").json()
    assert me["credit_percent_used"] == _old_percent(
        int(after.credit_balance), int(after.credit_used)
    )
