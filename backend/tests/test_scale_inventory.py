"""P10-01: Scale/compliance inventory validation tests."""

from pathlib import Path

DOCS_DIR = Path(__file__).resolve().parent.parent.parent / "docs"


class TestScaleInventoryDoc:
    def test_doc_exists(self):
        assert (DOCS_DIR / "P10_SCALE_COMPLIANCE_INVENTORY.md").exists()

    def test_documents_gaps(self):
        doc = (DOCS_DIR / "P10_SCALE_COMPLIANCE_INVENTORY.md").read_text(encoding="utf-8")
        for gap in ["GAP-S1", "GAP-S2", "GAP-S3", "GAP-S4", "GAP-S5"]:
            assert gap in doc


class TestExistingScaleFoundations:
    def test_cache_service_exists(self):
        from app.services.cache_service import CacheService
        svc = CacheService()
        assert hasattr(svc, "get")
        assert hasattr(svc, "set")

    def test_redis_config_exists(self):
        from app.core.config import Settings
        s = Settings(_env_file=None, postgres_password="x")
        assert hasattr(s, "redis_host")
        assert hasattr(s, "redis_port")

    def test_tenant_region_field_exists(self):
        from app.models.tenant import TenantORM
        assert hasattr(TenantORM, "region")

    def test_alert_service_exists(self):
        from app.services.alert_service import AlertService
        assert hasattr(AlertService, "emit")
