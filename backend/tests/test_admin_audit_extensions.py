"""Phase 2A — Commit 7: admin_audit_log extended columns (TDD).

Pins the new columns introduced by migration 028:

  outcome           VARCHAR(16)
  ip_address        VARCHAR(45)
  user_agent        VARCHAR(500)
  session_id_prefix VARCHAR(16) INDEX

plus the wiring in ``_record_cross_tenant_access`` that populates them.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import inspect

from app.models.admin_audit_log import AdminAuditLogORM


class TestSchemaExtension:
    def test_admin_audit_log_has_new_columns(self, db_inspector):
        cols = {c["name"] for c in db_inspector.get_columns("admin_audit_log")}
        assert "outcome" in cols
        assert "ip_address" in cols
        assert "user_agent" in cols
        assert "session_id_prefix" in cols

    def test_session_id_prefix_is_indexed(self, db_inspector):
        idx_names = {i["name"] for i in db_inspector.get_indexes("admin_audit_log")}
        assert any("session_id_prefix" in n for n in idx_names)


class TestExtendedRowRoundTrip:
    def test_can_insert_and_read_back(self, db_session):
        row = AdminAuditLogORM(
            actor_key_id="session:u-1",
            tenant_id="tenant-a",
            action="cross_tenant_access",
            resource_type="endpoint",
            resource_id="/admin/tenants",
            details="GET /admin/tenants — reason: super-admin audit",
            outcome="success",
            ip_address="203.0.113.42",
            user_agent="Mozilla/5.0 test",
            session_id_prefix="abcd1234",
        )
        db_session.add(row)
        db_session.flush()

        fetched = db_session.query(AdminAuditLogORM).filter_by(id=row.id).one()
        assert fetched.outcome == "success"
        assert fetched.ip_address == "203.0.113.42"
        assert fetched.user_agent == "Mozilla/5.0 test"
        assert fetched.session_id_prefix == "abcd1234"

    def test_legacy_row_without_new_fields_still_writes(self, db_session):
        """Old code paths that don't set the new columns must still be
        able to insert — the columns are nullable.
        """
        row = AdminAuditLogORM(
            actor_key_id="legacy:k-1",
            tenant_id="tenant-a",
            action="update_project",
            resource_type="project",
            resource_id="proj-a1",
        )
        db_session.add(row)
        db_session.flush()
        fetched = db_session.query(AdminAuditLogORM).filter_by(id=row.id).one()
        assert fetched.outcome is None
        assert fetched.ip_address is None


class TestCrossTenantAuditWriter:
    def test_records_row_with_new_fields(self, monkeypatch, sqlite_engine):
        """Call ``_record_cross_tenant_access`` against the same engine
        the test session uses, and confirm a row lands with the full
        metadata populated.
        """
        from sqlalchemy.orm import sessionmaker

        from app.api import admin_auth
        import app.core.db as db_mod

        # Force SessionLocal to bind to the test sqlite engine so the
        # writer sees the per-test table state.
        monkeypatch.setattr(
            db_mod, "SessionLocal", sessionmaker(bind=sqlite_engine)
        )

        admin_auth._record_cross_tenant_access(
            user_id="u-7",
            session_tenant_id="tenant-a",
            reason="super-admin dashboard",
            method="GET",
            path="/admin/tenants",
            ip_address="198.51.100.5",
            user_agent="ua-from-test",
            session_id_prefix="fff11111",
        )

        Session = sessionmaker(bind=sqlite_engine)
        session = Session()
        try:
            rows = session.query(AdminAuditLogORM).all()
            assert len(rows) == 1
            r = rows[0]
            assert r.action == "cross_tenant_access"
            assert r.resource_id == "/admin/tenants"
            assert r.outcome == "success"
            assert r.ip_address == "198.51.100.5"
            assert r.user_agent == "ua-from-test"
            assert r.session_id_prefix == "fff11111"
            assert r.actor_key_id == "session:u-7"
            assert r.tenant_id == "tenant-a"
        finally:
            session.close()
