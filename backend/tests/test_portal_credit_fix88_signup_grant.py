"""FIX88 — the signup bonus is a GRANT: a new user is born with
balance == quota == bonus, and the ledger row says ``signup_bonus``.
"""

from __future__ import annotations

import pytest

from app.models.portal_user import PortalUserORM
from app.models.system_setting import SystemSettingORM
from app.services.portal import auth_service, credit_service

from tests.fix88_support import Session, clean_portal_tables, ledger, make_user, reload


@pytest.fixture(autouse=True)
def _clean():
    db = Session()
    try:
        clean_portal_tables(db)
        yield
    finally:
        db.close()


def test_signup_bonus_sets_balance_and_quota_to_the_bonus():
    db = Session()
    try:
        r = auth_service.signup(db, "born@fix88.test", "Test1234!")
        db.commit()
        assert r["user"]["credit_balance"] == 100_000
        user = db.query(PortalUserORM).filter_by(email="born@fix88.test").one()
        assert (user.credit_balance, user.credit_quota, user.credit_used) == (
            100_000, 100_000, 0
        )
        rows = ledger(user.id)
        assert len(rows) == 1
        assert (rows[0].amount, rows[0].balance_after, rows[0].type, rows[0].created_by) == (
            100_000, 100_000, "signup_bonus", "system"
        )
    finally:
        db.close()


def test_signup_bonus_follows_the_setting_for_new_users_only():
    db = Session()
    try:
        db.get(SystemSettingORM, "free_trial_tokens").value = "250000"
        db.commit()
        auth_service.signup(db, "later@fix88.test", "Test1234!")
        db.commit()
        user = db.query(PortalUserORM).filter_by(email="later@fix88.test").one()
        assert (user.credit_balance, user.credit_quota) == (250_000, 250_000)
    finally:
        db.close()


def test_purchase_is_a_grant_that_raises_the_ceiling():
    db = Session()
    try:
        M = 1_000_000
        uid = make_user(db, balance=M, quota=M).id
        tx = credit_service.topup_credits(
            db, uid, 500_000, created_by="billing", description="pack", type="purchase"
        )
        db.commit()
        assert tx.type == "purchase" and tx.amount == 500_000
        after = reload(uid)
        assert (after.credit_balance, after.credit_quota) == (1_500_000, 1_500_000)
    finally:
        db.close()


def test_zero_bonus_creates_a_zero_ceiling_user():
    db = Session()
    try:
        db.get(SystemSettingORM, "free_trial_tokens").value = "0"
        db.commit()
        auth_service.signup(db, "none@fix88.test", "Test1234!")
        db.commit()
        user = db.query(PortalUserORM).filter_by(email="none@fix88.test").one()
        assert (user.credit_balance, user.credit_quota) == (0, 0)
        assert ledger(user.id) == []
    finally:
        db.close()
