"""Admin-audit-log aging tests.

RetentionService is now single-purpose: it ages the admin audit log,
scheduled through the nightly retention pipeline. The old
``cleanup_request_logs`` hard-deleter was removed deliberately — it
was never scheduled anywhere, and request_logs aging is owned by the
DLP stripper + archive mover + partition machinery (each with its own
tests). These tests pin the surviving behavior at FULL strength plus
the new safety shape:

* dry-run (the default) counts and deletes nothing;
* a live pass deletes exactly the out-of-window rows — the original
  backdated-row assertion, carried forward;
* a live pass records ITSELF in the audit log (aging leaves evidence);
* the request_logs deleter is really gone.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.services.admin_audit_service import AdminAuditService
from app.services.retention_service import RetentionService
import app.models  # noqa: F401


def _record_backdated(session, days_ago: int, action: str = "test") -> None:
    AdminAuditService().record(
        session, actor_key_id="k1", tenant_id="t1",
        action=action, resource_type="x", resource_id="y",
    )
    entry = session.execute(
        text(
            "SELECT id FROM admin_audit_log"
            " WHERE action = :a ORDER BY id DESC LIMIT 1"
        ),
        {"a": action},
    ).scalar()
    session.execute(
        text("UPDATE admin_audit_log SET timestamp = :ts WHERE id = :id"),
        {
            "ts": datetime.now(timezone.utc) - timedelta(days=days_ago),
            "id": entry,
        },
    )
    session.flush()


def _count(session) -> int:
    return int(
        session.execute(text("SELECT count(*) FROM admin_audit_log")).scalar()
        or 0
    )


class TestAuditLogAging:
    def test_dry_run_counts_and_deletes_nothing(self, db_session):
        _record_backdated(db_session, days_ago=400, action="old-a")
        _record_backdated(db_session, days_ago=1, action="new-a")
        before = _count(db_session)

        out = RetentionService(audit_log_days=365).cleanup_audit_logs(
            db_session, dry_run=True
        )
        assert out["dry_run"] is True
        assert out["rows_matched"] == 1
        assert out["rows_deleted"] == 0
        assert _count(db_session) == before

    def test_live_pass_deletes_only_out_of_window_rows(self, db_session):
        _record_backdated(db_session, days_ago=400, action="old-b")
        _record_backdated(db_session, days_ago=1, action="new-b")

        out = RetentionService(audit_log_days=365).cleanup_audit_logs(
            db_session, dry_run=False
        )
        assert out["rows_deleted"] == 1
        remaining = db_session.execute(
            text("SELECT action FROM admin_audit_log ORDER BY id")
        ).scalars().all()
        assert "old-b" not in remaining
        assert "new-b" in remaining

    def test_live_pass_records_itself(self, db_session):
        _record_backdated(db_session, days_ago=400, action="old-c")
        RetentionService(audit_log_days=365).cleanup_audit_logs(
            db_session, dry_run=False
        )
        rec = db_session.execute(
            text(
                "SELECT count(*) FROM admin_audit_log"
                " WHERE action = 'retention_delete'"
                "   AND resource_type = 'admin_audit_log'"
            )
        ).scalar()
        assert int(rec or 0) >= 1, "aging must leave its own audit record"

    def test_window_floor(self):
        with pytest.raises(ValueError):
            RetentionService(audit_log_days=0)

    def test_request_log_deleter_is_gone(self):
        """The unscheduled request_logs hard-deleter was retired — its
        job belongs to the stripper/mover/partition machinery. If it
        comes back it must come back scheduled, dry-run-first, and
        audited (and this pin removed deliberately)."""
        assert not hasattr(RetentionService, "cleanup_request_logs")
        assert not hasattr(RetentionService, "run_all")
