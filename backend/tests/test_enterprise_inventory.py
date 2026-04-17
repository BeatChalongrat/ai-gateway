"""P8-01: Enterprise controls inventory validation tests."""

from pathlib import Path

DOCS_DIR = Path(__file__).resolve().parent.parent.parent / "docs"


class TestEnterpriseInventoryDoc:
    def test_doc_exists(self):
        assert (DOCS_DIR / "P8_ENTERPRISE_INVENTORY.md").exists()

    def test_documents_gaps(self):
        doc = (DOCS_DIR / "P8_ENTERPRISE_INVENTORY.md").read_text(encoding="utf-8")
        for gap in ["GAP-E1", "GAP-E2", "GAP-E3", "GAP-E4", "GAP-E5"]:
            assert gap in doc


class TestExistingEnterpriseFoundations:
    def test_authorization_error_exists(self):
        from app.core.exceptions import AuthorizationError
        exc = AuthorizationError("test")
        assert exc.code == "authorization_error"

    def test_quota_error_exists(self):
        from app.core.exceptions import QuotaExceededError
        exc = QuotaExceededError("test")
        assert exc.code == "quota_exceeded"

    def test_budget_limit_field_exists(self):
        from app.models.project import ProjectORM
        assert hasattr(ProjectORM, "budget_limit")

    def test_usage_summary_available(self):
        from app.services.request_log_service import RequestLogService
        assert hasattr(RequestLogService, "usage_summary_by_project")
