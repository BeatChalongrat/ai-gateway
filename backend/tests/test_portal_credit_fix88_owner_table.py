"""FIX88 — the owner's table, driven through the admin ROUTES.

| operation            | quota before | balance before | after                    |
| top up 500k          | 1,000,000    | 500,000        | 1,000,000 / 1,000,000    |
| reset to full        | 1,000,000    | anything       | 1,000,000 / 1,000,000    |
| raise ceiling to 2M  | 1,000,000    | 1,000,000      | 2,000,000 / 2,000,000    |
| raise ceiling to 2M  | 1,000,000    | 500,000        | 1,500,000 / 2,000,000    |

Each row asserts balance / quota after AND the ledger row (amount,
balance_after, type, created_by = the admin label).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

from tests.fix88_support import Session, clean_portal_tables, ledger, make_user, reload


M = 1_000_000
ADMIN_LABEL = "gw_test_"  # conftest MOCK_AUTH.key_prefix


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


def _user(balance: int, quota: int) -> str:
    db = Session()
    try:
        return make_user(db, balance=balance, quota=quota).id
    finally:
        db.close()


def test_row1_topup_500k_lands_exactly_at_the_ceiling(client):
    uid = _user(balance=500_000, quota=M)
    r = client.post(
        f"/admin/portal-users/{uid}/topup",
        json={"amount": 500_000, "description": "row 1"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user"]["credit_balance"] == M
    assert body["user"]["credit_quota"] == M  # NOT 1.5M
    tx = body["transaction"]
    assert tx["amount"] == 500_000
    assert tx["balance_after"] == M
    assert tx["type"] == "manual_topup"
    assert tx["created_by"] == ADMIN_LABEL
    assert tx["description"] == "row 1"

    rows = ledger(uid)
    assert len(rows) == 1
    assert (rows[0].amount, rows[0].balance_after, rows[0].type, rows[0].created_by) == (
        500_000, M, "manual_topup", ADMIN_LABEL
    )
    after = reload(uid)
    assert (after.credit_balance, after.credit_quota) == (M, M)


def test_row2_reset_to_full_from_anything(client):
    uid = _user(balance=123_456, quota=M)
    r = client.post(f"/admin/portal-users/{uid}/reset", json={"description": "row 2"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["user"]["credit_balance"], body["user"]["credit_quota"]) == (M, M)
    tx = body["transaction"]
    assert tx["amount"] == M - 123_456
    assert tx["balance_after"] == M
    assert tx["type"] == "reset_to_full"
    assert tx["created_by"] == ADMIN_LABEL

    rows = ledger(uid)
    assert len(rows) == 1
    assert rows[0].amount == M - 123_456
    assert rows[0].balance_after == M
    assert rows[0].type == "reset_to_full"
    assert rows[0].created_by == ADMIN_LABEL


def test_row2_reset_via_topup_fill_flag_and_no_body(client):
    # ``{"fill": true}`` on /topup is the same operation.
    uid = _user(balance=0, quota=M)
    r = client.post(f"/admin/portal-users/{uid}/topup", json={"fill": True})
    assert r.status_code == 200, r.text
    assert r.json()["user"]["credit_balance"] == M
    assert r.json()["transaction"]["type"] == "reset_to_full"
    assert r.json()["transaction"]["amount"] == M
    # /reset with no body at all also works.
    uid2 = _user(balance=10, quota=M)
    r2 = client.post(f"/admin/portal-users/{uid2}/reset")
    assert r2.status_code == 200, r2.text
    assert r2.json()["user"]["credit_balance"] == M


def test_row3_raise_ceiling_to_2m_when_full(client):
    uid = _user(balance=M, quota=M)
    r = client.post(
        f"/admin/portal-users/{uid}/quota", json={"quota": 2 * M, "description": "row 3"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["user"]["credit_balance"], body["user"]["credit_quota"]) == (2 * M, 2 * M)
    tx = body["transaction"]
    assert tx["amount"] == M
    assert tx["balance_after"] == 2 * M
    assert tx["type"] == "adjustment"
    assert tx["created_by"] == ADMIN_LABEL

    rows = ledger(uid)
    assert len(rows) == 1
    assert (rows[0].amount, rows[0].balance_after, rows[0].type, rows[0].created_by) == (
        M, 2 * M, "adjustment", ADMIN_LABEL
    )


def test_row4_raise_ceiling_to_2m_when_half_full(client):
    uid = _user(balance=500_000, quota=M)
    r = client.post(f"/admin/portal-users/{uid}/quota", json={"quota": 2 * M})
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["user"]["credit_balance"], body["user"]["credit_quota"]) == (
        1_500_000,
        2 * M,
    )
    tx = body["transaction"]
    assert tx["amount"] == M
    assert tx["balance_after"] == 1_500_000
    assert tx["type"] == "adjustment"
    assert tx["created_by"] == ADMIN_LABEL
    # Default description names the ceiling move.
    assert "1,000,000" in tx["description"] and "2,000,000" in tx["description"]

    rows = ledger(uid)
    assert len(rows) == 1
    assert rows[0].amount == M
    assert rows[0].balance_after == 1_500_000


def test_detail_and_list_carry_credit_quota(client):
    uid = _user(balance=500_000, quota=M)
    detail = client.get(f"/admin/portal-users/{uid}").json()
    assert detail["credit_quota"] == M
    listed = [r for r in client.get("/admin/portal-users").json()["users"] if r["id"] == uid]
    assert listed[0]["credit_quota"] == M
