"""P8-01: the enterprise-control foundations the inventory named exist."""


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
