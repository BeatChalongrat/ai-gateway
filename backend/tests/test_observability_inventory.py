"""P6-01: Observability inventory validation tests."""

from pathlib import Path

DOCS_DIR = Path(__file__).resolve().parent.parent.parent / "docs"


class TestObservabilityInventoryDoc:
    def test_doc_exists(self):
        assert (DOCS_DIR / "P6_OBSERVABILITY_INVENTORY.md").exists()

    def test_documents_gaps(self):
        doc = (DOCS_DIR / "P6_OBSERVABILITY_INVENTORY.md").read_text(encoding="utf-8")
        for gap in ["GAP-O1", "GAP-O2", "GAP-O3", "GAP-O4", "GAP-O5"]:
            assert gap in doc

    def test_documents_privacy(self):
        doc = (DOCS_DIR / "P6_OBSERVABILITY_INVENTORY.md").read_text(encoding="utf-8")
        assert "privacy" in doc.lower() or "Privacy" in doc


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
