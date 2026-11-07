"""FIX88 — the ceiling's rules, through the service and the routes.

- a top-up beyond the ceiling is refused (422, ``credit_ceiling``,
  the room left named), the balance untouched, no ledger row;
- fill when already full writes no row (``transaction`` null);
- lowering the ceiling below the balance floors at 0 and records
  only what actually left;
- a negative ceiling is refused;
- bulk: one unknown id → nothing moves; totals and per-user results.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.exceptions import ValidationError
from app.main import app
from app.services.portal import credit_service
from app.services.portal.credit_service import CreditCeilingExceeded

from tests.fix88_support import Session, clean_portal_tables, ledger, make_user, reload


M = 1_000_000


@pytest.fixture(autouse=True)
def _clean():
    db = Session()
    try:
        clean_portal_tables(db)
        yield
    finally:
        db.close()


@pytest.fixture
def client():
    return TestClient(app)


def _user(balance: int, quota: int, used: int = 0) -> str:
    db = Session()
    try:
        return make_user(db, balance=balance, quota=quota, used=used).id
    finally:
        db.close()


# --- the cap ---------------------------------------------------------------


def test_topup_beyond_ceiling_is_refused_and_writes_nothing(client):
    uid = _user(balance=800_000, quota=M)
    r = client.post(f"/admin/portal-users/{uid}/topup", json={"amount": 300_000})
    assert r.status_code == 422, r.text
    err = r.json()["error"]
    assert err["code"] == "credit_ceiling"
    assert "200,000" in err["message"]  # the room left, named
    assert err["details"]["room"] == 200_000
    assert err["details"]["balance"] == 800_000
    assert err["details"]["quota"] == M
    assert err["details"]["amount"] == 300_000

    after = reload(uid)
    assert (after.credit_balance, after.credit_quota) == (800_000, M)
    assert ledger(uid) == []


def test_topup_exactly_to_the_ceiling_is_allowed(client):
    uid = _user(balance=800_000, quota=M)
    r = client.post(f"/admin/portal-users/{uid}/topup", json={"amount": 200_000})
    assert r.status_code == 200, r.text
    assert r.json()["user"]["credit_balance"] == M


def test_topup_on_a_zero_ceiling_user_is_refused(client):
    # A user who was never granted anything has no room; the admin
    # raises the ceiling instead (which fills by the same amount).
    uid = _user(balance=0, quota=0)
    r = client.post(f"/admin/portal-users/{uid}/topup", json={"amount": 1})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "credit_ceiling"
    r2 = client.post(f"/admin/portal-users/{uid}/quota", json={"quota": 500_000})
    assert r2.status_code == 200
    assert (r2.json()["user"]["credit_balance"], r2.json()["user"]["credit_quota"]) == (
        500_000, 500_000
    )


def test_topup_needs_amount_unless_fill(client):
    uid = _user(balance=0, quota=M)
    r = client.post(f"/admin/portal-users/{uid}/topup", json={})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "amount_required"
    assert ledger(uid) == []


def test_service_cap_raises_domain_error():
    db = Session()
    try:
        uid = make_user(db, balance=10, quota=20).id
        with pytest.raises(CreditCeilingExceeded) as exc:
            credit_service.topup_credits(db, uid, 11, created_by="a")
        assert exc.value.code == "credit_ceiling"
        assert isinstance(exc.value, ValidationError)
        assert exc.value.details["room"] == 10
        db.rollback()
        assert reload(uid).credit_balance == 10
        assert ledger(uid) == []
        # cap_at_quota=False turns it into a grant (ceiling moves too).
        tx = credit_service.topup_credits(db, uid, 11, created_by="a", cap_at_quota=False)
        db.commit()
        assert tx.amount == 11
        after = reload(uid)
        assert (after.credit_balance, after.credit_quota) == (21, 31)
        # cap_at_quota=True on a grant type caps it.
        with pytest.raises(CreditCeilingExceeded):
            credit_service.topup_credits(
                db, uid, 11, created_by="a", type="purchase", cap_at_quota=True
            )
        db.rollback()
    finally:
        db.close()


# --- fill ------------------------------------------------------------------


def test_fill_when_already_full_writes_no_row(client):
    uid = _user(balance=M, quota=M)
    r = client.post(f"/admin/portal-users/{uid}/reset")
    assert r.status_code == 200, r.text
    assert r.json()["transaction"] is None
    assert r.json()["user"]["credit_balance"] == M
    assert ledger(uid) == []
    r2 = client.post(f"/admin/portal-users/{uid}/topup", json={"fill": True})
    assert r2.status_code == 200
    assert r2.json()["transaction"] is None
    assert ledger(uid) == []


# --- lowering --------------------------------------------------------------


def test_lowering_below_balance_floors_at_zero_and_records_only_what_left(client):
    uid = _user(balance=800_000, quota=M, used=200_000)
    r = client.post(f"/admin/portal-users/{uid}/quota", json={"quota": 100_000})
    assert r.status_code == 200, r.text
    body = r.json()
    # delta = 100k − 1M = −900k, but only 800k was there to leave.
    assert (body["user"]["credit_balance"], body["user"]["credit_quota"]) == (0, 100_000)
    assert body["user"]["credit_used"] == 200_000  # usage is not a refund
    tx = body["transaction"]
    assert tx["amount"] == -800_000
    assert tx["balance_after"] == 0
    assert tx["type"] == "adjustment"
    rows = ledger(uid)
    assert len(rows) == 1 and rows[0].amount == -800_000


def test_lowering_when_balance_is_zero_moves_the_ceiling_only(client):
    uid = _user(balance=0, quota=M, used=M)
    r = client.post(f"/admin/portal-users/{uid}/quota", json={"quota": 10})
    assert r.status_code == 200, r.text
    assert r.json()["transaction"] is None
    assert (r.json()["user"]["credit_balance"], r.json()["user"]["credit_quota"]) == (0, 10)
    assert ledger(uid) == []


def test_lowering_within_room_removes_exactly_the_delta(client):
    uid = _user(balance=900_000, quota=M)
    r = client.post(f"/admin/portal-users/{uid}/quota", json={"quota": 700_000})
    assert r.status_code == 200
    assert (r.json()["user"]["credit_balance"], r.json()["user"]["credit_quota"]) == (
        600_000, 700_000
    )
    assert r.json()["transaction"]["amount"] == -300_000


def test_same_quota_is_a_no_op(client):
    uid = _user(balance=5, quota=M)
    r = client.post(f"/admin/portal-users/{uid}/quota", json={"quota": M})
    assert r.status_code == 200
    assert r.json()["transaction"] is None
    assert ledger(uid) == []


def test_negative_quota_refused(client):
    uid = _user(balance=5, quota=M)
    r = client.post(f"/admin/portal-users/{uid}/quota", json={"quota": -1})
    assert r.status_code == 422, r.text
    assert reload(uid).credit_quota == M
    db = Session()
    try:
        with pytest.raises(ValidationError) as exc:
            credit_service.set_quota(db, uid, -5, created_by="a")
        assert exc.value.code == "invalid_quota"
    finally:
        db.close()


def test_unknown_user_404_on_every_route(client):
    for path, body in [
        ("/admin/portal-users/nope/topup", {"amount": 1}),
        ("/admin/portal-users/nope/reset", None),
        ("/admin/portal-users/nope/quota", {"quota": 1}),
    ]:
        r = client.post(path, json=body) if body is not None else client.post(path)
        assert r.status_code == 404, (path, r.text)
        assert r.json()["error"]["code"] == "user_not_found"


# --- bulk ------------------------------------------------------------------


def test_bulk_one_unknown_id_moves_nothing(client):
    a = _user(balance=0, quota=M)
    b = _user(balance=0, quota=M)
    r = client.post(
        "/admin/portal-users/bulk",
        json={"user_ids": [a, "does-not-exist", b], "action": "fill"},
    )
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "user_not_found"
    assert r.json()["error"]["details"]["user_id"] == "does-not-exist"
    assert reload(a).credit_balance == 0
    assert reload(b).credit_balance == 0
    assert ledger(a) == [] and ledger(b) == []


def test_bulk_missing_amount_or_quota_is_422_and_moves_nothing(client):
    a = _user(balance=0, quota=M)
    r = client.post("/admin/portal-users/bulk", json={"user_ids": [a], "action": "topup"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "amount_required"
    r = client.post("/admin/portal-users/bulk", json={"user_ids": [a], "action": "quota"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "quota_required"
    assert ledger(a) == []


def test_bulk_bounds(client):
    r = client.post("/admin/portal-users/bulk", json={"user_ids": [], "action": "fill"})
    assert r.status_code == 422
    r = client.post(
        "/admin/portal-users/bulk",
        json={"user_ids": [f"u{i}" for i in range(501)], "action": "fill"},
    )
    assert r.status_code == 422
    r = client.post("/admin/portal-users/bulk", json={"user_ids": ["x"], "action": "nuke"})
    assert r.status_code == 422


def test_bulk_topup_totals_and_per_user_ceiling(client):
    a = _user(balance=0, quota=M)  # room 1M
    b = _user(balance=950_000, quota=M)  # room 50k → refused
    c = _user(balance=M, quota=M)  # full → refused
    r = client.post(
        "/admin/portal-users/bulk",
        json={"user_ids": [a, b, c, a], "action": "topup", "amount": 100_000},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    results = {x["user_id"]: x for x in body["results"]}
    assert len(body["results"]) == 3  # duplicate id collapsed
    assert results[a] == {
        "user_id": a, "ok": True, "balance_after": 100_000, "quota_after": M, "moved": 100_000
    }
    assert results[b]["ok"] is False
    assert results[b]["error"] == "credit_ceiling"
    assert "50,000" in results[b]["message"]
    assert results[b]["balance_after"] == 950_000 and results[b]["moved"] == 0
    assert results[c]["ok"] is False and results[c]["moved"] == 0
    assert body["total_moved"] == 100_000
    assert body["reduced_count"] == 0
    assert len(ledger(a)) == 1 and ledger(b) == [] and ledger(c) == []
    assert ledger(a)[0].created_by == "gw_test_"


def test_bulk_fill_and_reset_are_the_same(client):
    a = _user(balance=1, quota=M)
    b = _user(balance=M, quota=M)
    for action in ("fill", "reset"):
        r = client.post(
            "/admin/portal-users/bulk", json={"user_ids": [a, b], "action": action}
        )
        assert r.status_code == 200, r.text
    # First call moved a by M−1 and b by 0; second call moved nothing.
    assert reload(a).credit_balance == M
    assert len(ledger(a)) == 1 and ledger(a)[0].type == "reset_to_full"
    assert ledger(b) == []


def test_bulk_quota_lowering_counts_reductions(client):
    a = _user(balance=M, quota=M)  # → 500k, −500k
    b = _user(balance=100_000, quota=M)  # → 100k stays (delta −500k, room)… no: 100k−500k floors
    c = _user(balance=0, quota=200_000)  # ceiling rises → +300k
    r = client.post(
        "/admin/portal-users/bulk",
        json={"user_ids": [a, b, c], "action": "quota", "quota": 500_000, "description": "class"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    results = {x["user_id"]: x for x in body["results"]}
    assert results[a]["moved"] == -500_000 and results[a]["balance_after"] == 500_000
    assert results[b]["moved"] == -100_000 and results[b]["balance_after"] == 0
    assert results[c]["moved"] == 300_000 and results[c]["balance_after"] == 300_000
    for uid in (a, b, c):
        assert results[uid]["quota_after"] == 500_000
        assert reload(uid).credit_quota == 500_000
    assert body["total_moved"] == -500_000 - 100_000 + 300_000
    assert body["reduced_count"] == 2
    assert ledger(a)[0].description == "class"
