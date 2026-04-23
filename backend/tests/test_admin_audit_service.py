"""P8-07: Policy versioning and admin audit trail tests."""

import json
import uuid

import pytest

from app.models.admin_audit_log import AdminAuditLogORM
from app.services.admin_audit_service import AdminAuditService
import app.models  # noqa: F401


@pytest.fixture()
def svc():
    return AdminAuditService()


class TestAdminAuditRecord:
    def test_record_creates_entry(self, db_session, svc):
        entry = svc.record(
            db_session,
            actor_key_id="k1",
            tenant_id="t1",
            action="policy_assign",
            resource_type="project",
            resource_id="p1",
            details={"old_profile": "default", "new_profile": "strict"},
        )
        assert entry.id is not None
        assert entry.action == "policy_assign"

    def test_record_details_serialized(self, db_session, svc):
        entry = svc.record(
            db_session,
            actor_key_id="k1",
            tenant_id="t1",
            action="key_revoke",
            resource_type="api_key",
            resource_id="k2",
            details={"reason": "compromised"},
        )
        parsed = json.loads(entry.details)
        assert parsed["reason"] == "compromised"


class TestAdminAuditQuery:
    def test_list_by_tenant(self, db_session, svc):
        for i in range(3):
            svc.record(
                db_session,
                actor_key_id="k1", tenant_id="t1",
                action=f"action_{i}", resource_type="project", resource_id=f"p{i}",
            )
        results = svc.list_by_tenant(db_session, "t1")
        assert len(results) == 3

    def test_list_by_action(self, db_session, svc):
        svc.record(db_session, actor_key_id="k1", tenant_id="t1",
                    action="key_create", resource_type="api_key", resource_id="k1")
        svc.record(db_session, actor_key_id="k1", tenant_id="t1",
                    action="key_revoke", resource_type="api_key", resource_id="k2")
        results = svc.list_by_action(db_session, "key_revoke")
        assert len(results) == 1
        assert results[0].action == "key_revoke"


class TestAdminAuditLogSchema:
    def test_table_exists(self, db_inspector):
        assert "admin_audit_log" in db_inspector.get_table_names()

    def test_columns(self, db_inspector):
        cols = {c["name"] for c in db_inspector.get_columns("admin_audit_log")}
        # Phase 2A Commit 7 / migration 028 added outcome, ip_address,
        # user_agent, session_id_prefix. See test_admin_audit_extensions
        # for the full round-trip coverage of those.
        expected = {"id", "timestamp", "actor_key_id", "tenant_id", "action",
                    "resource_type", "resource_id", "details",
                    "outcome", "ip_address", "user_agent", "session_id_prefix"}
        assert expected == cols
