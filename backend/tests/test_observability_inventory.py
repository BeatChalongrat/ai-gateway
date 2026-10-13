"""P6-01: the observability pieces the inventory named exist."""


class TestExistingObservability:
    def test_audit_service_importable(self):
        from app.services.audit_service import AuditService
        assert hasattr(AuditService, "emit_dlp_findings")
        assert hasattr(AuditService, "record_blocked_request")
        assert hasattr(AuditService, "record_success")

    def test_request_log_orm_exists(self):
        from app.models.request_log import RequestLogORM
        assert RequestLogORM.__tablename__ == "request_logs"

    def test_usage_service_exists(self):
        from app.services.usage_service import UsageService
        assert hasattr(UsageService, "estimate")

    def test_routing_trace_available(self):
        from app.domain.routing import RoutingResult, ModelCandidate
        r = RoutingResult(selected=ModelCandidate(provider="p", model="m"))
        trace = r.to_trace_dict()
        assert "selected" in trace
