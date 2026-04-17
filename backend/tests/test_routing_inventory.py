"""P5-01: Routing inventory validation tests.

Verifies:
- Inventory document exists and references key components
- Current routing service exists and is callable
- Model registry is accessible
- Scattered auto-resolution in adapters is documented
"""

from pathlib import Path

DOCS_DIR = Path(__file__).resolve().parent.parent.parent / "docs"


class TestRoutingInventoryDoc:
    def test_doc_exists(self):
        assert (DOCS_DIR / "P5_ROUTING_INVENTORY.md").exists()

    def test_documents_routing_service(self):
        doc = (DOCS_DIR / "P5_ROUTING_INVENTORY.md").read_text(encoding="utf-8")
        assert "RoutingService" in doc

    def test_documents_model_registry(self):
        doc = (DOCS_DIR / "P5_ROUTING_INVENTORY.md").read_text(encoding="utf-8")
        assert "model_registry" in doc

    def test_documents_adapter_auto_resolution(self):
        doc = (DOCS_DIR / "P5_ROUTING_INVENTORY.md").read_text(encoding="utf-8")
        assert "auto" in doc.lower()
        assert "adapter" in doc.lower()

    def test_documents_gaps(self):
        doc = (DOCS_DIR / "P5_ROUTING_INVENTORY.md").read_text(encoding="utf-8")
        for gap in ["GAP-R1", "GAP-R2", "GAP-R3", "GAP-R4", "GAP-R5"]:
            assert gap in doc, f"Missing gap: {gap}"


class TestCurrentRoutingServiceExists:
    def test_routing_service_importable(self):
        from app.services.routing_service import RoutingService
        svc = RoutingService()
        assert hasattr(svc, "select_route")

    def test_model_registry_importable(self):
        from app.services.providers.model_registry import resolve_provider, list_models
        assert resolve_provider("gpt-4o") == "openai"
        assert len(list_models()) > 0

    def test_route_decision_importable(self):
        from app.domain.entities import RouteDecision
        rd = RouteDecision(provider_name="openai", model_name="gpt-4o")
        assert rd.provider_name == "openai"
