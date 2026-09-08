"""Shared fixtures for the FIX88 credit-ceiling tests (not collected —
no ``test_`` prefix)."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import sessionmaker

from app.models.api_key import ApiKeyORM
from app.models.credit_transaction import CreditTransactionORM
from app.models.portal_user import PortalUserORM
from app.models.system_setting import SystemSettingORM

from tests.conftest import _integration_engine


Session = sessionmaker(bind=_integration_engine)

# The owner's real numbers (FIX86 measured them live).
OWNER_BALANCE = 52_234_358
OWNER_USED = 47_915_642
OWNER_QUOTA_BACKFILLED = OWNER_BALANCE + OWNER_USED  # 100,150,000


def clean_portal_tables(db) -> None:
    db.query(CreditTransactionORM).delete()
    db.query(ApiKeyORM).filter(ApiKeyORM.portal_user_id.isnot(None)).delete()
    db.query(PortalUserORM).delete()
    db.query(SystemSettingORM).delete()
    db.add_all(
        [
            SystemSettingORM(key="free_trial_tokens", value="100000"),
            SystemSettingORM(key="portal_signup_enabled", value="true"),
        ]
    )
    db.commit()


def make_user(
    db,
    *,
    email: str | None = None,
    balance: int = 0,
    used: int = 0,
    quota: int | None = None,
) -> PortalUserORM:
    """Insert a portal user the way migration 088 leaves a real row:
    ``quota`` defaults to ``balance + used`` (the backfill value)."""
    user = PortalUserORM(
        id=str(uuid.uuid4()),
        email=email or f"{uuid.uuid4().hex[:10]}@fix88.test",
        password_hash="dummy",
        credit_balance=balance,
        credit_used=used,
        credit_quota=(balance + used) if quota is None else quota,
        plan="free_trial",
        status="active",
        tenant_id="test-tenant",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def make_key(db, user_id: str, key_id: str) -> ApiKeyORM:
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
    db.commit()
    return key


def reload(user_id: str) -> PortalUserORM:
    db = Session()
    try:
        return db.get(PortalUserORM, user_id)
    finally:
        db.close()


def ledger(user_id: str) -> list[CreditTransactionORM]:
    db = Session()
    try:
        return (
            db.query(CreditTransactionORM)
            .filter(CreditTransactionORM.user_id == user_id)
            .order_by(CreditTransactionORM.created_at.asc())
            .all()
        )
    finally:
        db.close()
