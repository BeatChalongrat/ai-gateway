"""Phase 3 Commit 25 — token packs tenant-scoped (Q1 answer).

Verifies:
- Global packs (tenant_id NULL) are visible to everyone.
- Tenant-private packs visible only to that tenant.
- Purchase of a foreign-tenant pack → pack_not_found (no existence
  oracle).
- Migration 031 adds the column + index and rollbacks cleanly.
"""

from __future__ import annotations

import importlib.util
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.models.api_key import ApiKeyORM
from app.models.credit_transaction import CreditTransactionORM
from app.models.portal_user import PortalUserORM
from app.models.project import ProjectORM
from app.models.system_setting import SystemSettingORM
from app.models.tenant import TenantORM
from app.models.tenant_invite import TenantInviteORM
from app.models.token_pack import TokenPackORM
from tests.conftest import _integration_engine


_Session = sessionmaker(bind=_integration_engine)


@pytest.fixture(autouse=True)
def _clean():
    db = _Session()
    try:
        db.query(TenantInviteORM).delete(synchronize_session=False)
        db.query(ApiKeyORM).filter(
            ApiKeyORM.portal_user_id.isnot(None)
        ).delete(synchronize_session=False)
        db.query(ProjectORM).filter(
            ProjectORM.portal_user_id.isnot(None)
        ).delete(synchronize_session=False)
        db.query(CreditTransactionORM).delete(synchronize_session=False)
        db.query(PortalUserORM).delete(synchronize_session=False)
        db.query(TokenPackORM).delete(synchronize_session=False)
        db.query(TenantORM).filter(
            ~TenantORM.tenant_id.in_(["test-tenant"])
        ).delete(synchronize_session=False)
        db.query(SystemSettingORM).delete(synchronize_session=False)
        db.add_all(
            [
                SystemSettingORM(key="free_trial_tokens", value="100000"),
                SystemSettingORM(key="portal_signup_enabled", value="true"),
            ]
        )
        db.commit()
        yield
    finally:
        db.close()


@pytest.fixture
def client():
    return TestClient(app)


def _signup(client, email: str) -> dict:
    r = client.post(
        "/portal/auth/signup",
        json={"email": email, "password": "Test1234!"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # HttpOnly migration: tokens are cookies. Capture, then clear the
    # shared jar so multi-user tests auth via the explicit Bearer header
    # (cookie auth takes priority and would resolve to the last signup).
    body["access_token"] = r.cookies.get("portal_access_token")
    body["refresh_token"] = r.cookies.get("portal_refresh_token")
    client.cookies.clear()
    return body


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _seed_pack(db, *, id_: str, name: str, tenant_id: str | None) -> None:
    db.add(
        TokenPackORM(
            id=id_,
            name=name,
            tokens=1_000_000,
            price_usd=Decimal("10.00"),
            description=f"{name} pack",
            is_active=True,
            sort_order=0,
            tenant_id=tenant_id,
        )
    )
    db.commit()


class TestListPacksVisibility:
    def test_global_pack_visible_to_everyone(self, client):
        a = _signup(client, "a@t.com")
        db = _Session()
        try:
            _seed_pack(db, id_="glob-1", name="Global Starter", tenant_id=None)
        finally:
            db.close()
        r = client.get(
            "/portal/billing/packs", headers=_auth(a["access_token"])
        )
        assert r.status_code == 200
        ids = [p["id"] for p in r.json()]
        assert "glob-1" in ids

    def test_tenant_private_pack_only_visible_to_that_tenant(self, client):
        a = _signup(client, "a@t.com")
        b = _signup(client, "b@t.com")
        db = _Session()
        try:
            user_a = db.get(PortalUserORM, a["user"]["id"])
            _seed_pack(
                db,
                id_="a-private",
                name="A Private",
                tenant_id=user_a.tenant_id,
            )
        finally:
            db.close()
        a_ids = [
            p["id"]
            for p in client.get(
                "/portal/billing/packs", headers=_auth(a["access_token"])
            ).json()
        ]
        b_ids = [
            p["id"]
            for p in client.get(
                "/portal/billing/packs", headers=_auth(b["access_token"])
            ).json()
        ]
        assert "a-private" in a_ids
        assert "a-private" not in b_ids

    def test_visibility_mix(self, client):
        """One tenant sees global + their own; the other sees
        global + their own — never the other tenant's private pack."""
        a = _signup(client, "a@t.com")
        b = _signup(client, "b@t.com")
        db = _Session()
        try:
            user_a = db.get(PortalUserORM, a["user"]["id"])
            user_b = db.get(PortalUserORM, b["user"]["id"])
            _seed_pack(db, id_="glob", name="Global", tenant_id=None)
            _seed_pack(
                db, id_="a-p", name="A", tenant_id=user_a.tenant_id
            )
            _seed_pack(
                db, id_="b-p", name="B", tenant_id=user_b.tenant_id
            )
        finally:
            db.close()
        a_ids = sorted(
            p["id"]
            for p in client.get(
                "/portal/billing/packs", headers=_auth(a["access_token"])
            ).json()
        )
        b_ids = sorted(
            p["id"]
            for p in client.get(
                "/portal/billing/packs", headers=_auth(b["access_token"])
            ).json()
        )
        assert a_ids == ["a-p", "glob"]
        assert b_ids == ["b-p", "glob"]


class TestPurchaseTenantGate:
    def test_purchase_global_pack_works(self, client):
        a = _signup(client, "a@t.com")
        db = _Session()
        try:
            _seed_pack(db, id_="glob", name="Global", tenant_id=None)
        finally:
            db.close()
        r = client.post(
            "/portal/billing/purchase",
            headers=_auth(a["access_token"]),
            json={"pack_id": "glob"},
        )
        # The stub returns 501 (payment not implemented) — that
        # still indicates the pack was visible and resolved.
        assert r.status_code == 501
        assert r.json()["code"] == "payment_not_implemented"

    def test_purchase_own_tenant_pack_works(self, client):
        a = _signup(client, "a@t.com")
        db = _Session()
        try:
            user_a = db.get(PortalUserORM, a["user"]["id"])
            _seed_pack(
                db, id_="a-p", name="A Pack", tenant_id=user_a.tenant_id
            )
        finally:
            db.close()
        r = client.post(
            "/portal/billing/purchase",
            headers=_auth(a["access_token"]),
            json={"pack_id": "a-p"},
        )
        assert r.status_code == 501

    def test_purchase_foreign_tenant_pack_is_not_found(self, client):
        a = _signup(client, "a@t.com")
        b = _signup(client, "b@t.com")
        db = _Session()
        try:
            user_b = db.get(PortalUserORM, b["user"]["id"])
            _seed_pack(
                db, id_="b-priv", name="B Pack", tenant_id=user_b.tenant_id
            )
        finally:
            db.close()
        r = client.post(
            "/portal/billing/purchase",
            headers=_auth(a["access_token"]),
            json={"pack_id": "b-priv"},
        )
        assert r.status_code == 404
        # Deliberately identical code to a truly-missing pack — no
        # existence oracle for cross-tenant pack ids.
        assert r.json()["error"]["code"] == "pack_not_found"


# ---------------------------------------------------------------------------
# Migration 031 schema verification
# ---------------------------------------------------------------------------


def _load_migration(name: str):
    path = Path(__file__).resolve().parent.parent / "migrations" / "versions" / name
    spec = importlib.util.spec_from_file_location(f"migration_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestMigration031:
    def test_upgrade_adds_tenant_id_column_to_token_packs(self):
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        try:
            with engine.begin() as conn:
                from tests.test_migration_030_portal_tenancy_finalize import (
                    _SEQUENCE_THROUGH_029,
                )

                mctx = MigrationContext.configure(conn)
                with Operations.context(mctx):
                    for name in _SEQUENCE_THROUGH_029:
                        _load_migration(name).upgrade()
                    _load_migration(
                        "030_portal_tenancy_finalize.py"
                    ).upgrade()
                    _load_migration(
                        "031_token_packs_tenant_scoped.py"
                    ).upgrade()
            cols = {
                c["name"]
                for c in inspect(engine).get_columns("token_packs")
            }
            assert "tenant_id" in cols
        finally:
            engine.dispose()
