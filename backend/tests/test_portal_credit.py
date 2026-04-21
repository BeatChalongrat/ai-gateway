"""Credit ledger + gateway credit-check tests."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import sessionmaker

from app.core.exceptions import AuthorizationError, QuotaExceededError
from app.models.api_key import ApiKeyORM
from app.models.credit_transaction import CreditTransactionORM
from app.models.portal_user import PortalUserORM
from app.models.system_setting import SystemSettingORM
from app.services.portal import auth_service, credit_service
from app.services.gateway.portal_credit_check import (
    check_portal_credit,
    deduct_portal_credit,
)

from tests.conftest import _integration_engine


_Session = sessionmaker(bind=_integration_engine)


@pytest.fixture(autouse=True)
def _clean():
    db = _Session()
    try:
        db.query(CreditTransactionORM).delete()
        # delete portal-owned keys, leave test-tenant admin seed keys alone
        db.query(ApiKeyORM).filter(ApiKeyORM.portal_user_id.isnot(None)).delete()
        db.query(PortalUserORM).delete()
        db.query(SystemSettingORM).delete()
        db.add_all([
            SystemSettingORM(key="free_trial_tokens", value="100000"),
            SystemSettingORM(key="portal_signup_enabled", value="true"),
        ])
        db.commit()
        yield
    finally:
        db.close()


def _make_user(db, email="u@test.com", balance=1000) -> PortalUserORM:
    user = PortalUserORM(
        id=str(uuid.uuid4()),
        email=email,
        password_hash="dummy",
        credit_balance=balance,
        credit_used=0,
        plan="free_trial",
        status="active",
    )
    db.add(user)
    db.flush()
    return user


def _make_key(db, user_id: str, key_id: str = "portal-key-1") -> ApiKeyORM:
    key = ApiKeyORM(
        key_id=key_id,
        key_prefix="gw_t_",
        key_hash="h_" + key_id,
        tenant_id="test-tenant",
        project_id="test-project",
        name="t",
        role="user",
        status="active",
        portal_user_id=user_id,
    )
    db.add(key)
    db.flush()
    return key


def test_deduct_credits_updates_balance_and_logs_transaction():
    db = _Session()
    try:
        user = _make_user(db, balance=500)
        tx = credit_service.deduct_credits(db, user.id, 100, request_id="r-1")
        db.flush()
        db.refresh(user)
        assert user.credit_balance == 400
        assert user.credit_used == 100
        assert tx is not None
        assert tx.amount == -100
        assert tx.balance_after == 400
        assert tx.type == "usage"
        assert tx.request_id == "r-1"
    finally:
        db.rollback()
        db.close()


def test_deduct_credits_clamps_at_zero():
    db = _Session()
    try:
        user = _make_user(db, balance=50)
        credit_service.deduct_credits(db, user.id, 200)
        db.flush()
        db.refresh(user)
        assert user.credit_balance == 0
        assert user.credit_used == 50  # actual deducted, not attempted
    finally:
        db.rollback()
        db.close()


def test_topup_credits_increases_balance():
    db = _Session()
    try:
        user = _make_user(db, balance=100)
        tx = credit_service.topup_credits(
            db, user.id, 5000, created_by="admin@ex.com",
            description="refill",
        )
        db.flush()
        db.refresh(user)
        assert user.credit_balance == 5100
        assert tx.type == "manual_topup"
        assert tx.amount == 5000
        assert tx.balance_after == 5100
        assert tx.created_by == "admin@ex.com"
    finally:
        db.rollback()
        db.close()


def test_gateway_returns_402_when_credit_zero():
    """check_portal_credit raises QuotaExceededError when balance <= 0."""
    db = _Session()
    try:
        user = _make_user(db, balance=0)
        _make_key(db, user.id, key_id="zero-key")
        db.commit()
        with pytest.raises(QuotaExceededError) as excinfo:
            check_portal_credit(db, "zero-key")
        assert excinfo.value.code == "insufficient_credits"
    finally:
        db.rollback()
        db.close()


def test_gateway_rejects_suspended_user():
    db = _Session()
    try:
        user = _make_user(db, balance=1000)
        user.status = "suspended"
        _make_key(db, user.id, key_id="susp-key")
        db.commit()
        with pytest.raises(AuthorizationError) as excinfo:
            check_portal_credit(db, "susp-key")
        assert excinfo.value.code == "account_suspended"
    finally:
        db.rollback()
        db.close()


def test_gateway_skips_credit_check_for_non_portal_keys():
    """Key without portal_user_id — function must be a no-op (no raise)."""
    db = _Session()
    try:
        key = ApiKeyORM(
            key_id="admin-key-x",
            key_prefix="gw_a_",
            key_hash="h_admin_x",
            tenant_id="test-tenant",
            project_id="test-project",
            name="admin",
            role="admin",
            status="active",
            portal_user_id=None,
        )
        db.add(key)
        db.commit()
        check_portal_credit(db, "admin-key-x")  # must not raise
        deduct_portal_credit(db, "admin-key-x", 500, "r-x")  # must not raise
    finally:
        db.rollback()
        db.close()


def test_deduct_via_gateway_hook():
    db = _Session()
    try:
        user = _make_user(db, balance=1000)
        _make_key(db, user.id, key_id="deduct-key")
        db.commit()
        deduct_portal_credit(db, "deduct-key", 250, "req-9")
        db.refresh(user)
        assert user.credit_balance == 750
        assert user.credit_used == 250
    finally:
        db.rollback()
        db.close()


def test_free_trial_amount_reads_from_system_settings():
    """Changing the setting affects ONLY new signups, not existing users."""
    db = _Session()
    try:
        # First user with default 100000.
        r1 = auth_service.signup(db, "first@test.com", "Test1234!")
        db.commit()
        assert r1["user"]["credit_balance"] == 100000

        # Admin bumps the setting.
        setting = db.get(SystemSettingORM, "free_trial_tokens")
        setting.value = "200000"
        db.commit()

        # Second user gets the new amount; first is untouched.
        r2 = auth_service.signup(db, "second@test.com", "Test1234!")
        db.commit()
        assert r2["user"]["credit_balance"] == 200000

        first = db.query(PortalUserORM).filter_by(email="first@test.com").one()
        assert first.credit_balance == 100000
    finally:
        db.rollback()
        db.close()
