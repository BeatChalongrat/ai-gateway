"""P5-10: Routing documentation validation tests."""

from pathlib import Path

DOCS_DIR = Path(__file__).resolve().parent.parent.parent / "docs"


class TestRoutingDocsExist:
    def test_doc_exists(self):
        assert (DOCS_DIR / "ROUTING_ARCHITECTURE.md").exists()

    def test_documents_key_concepts(self):
        doc = (DOCS_DIR / "ROUTING_ARCHITECTURE.md").read_text(encoding="utf-8")
        for concept in [
            "RoutingRequest",
            "RoutingResult",
            "ModelCandidate",
            "fallback",
            "cost_tier",
            "latency_tier",
            "model_registry",
        ]:
            assert concept in doc, f"Missing concept: {concept}"

    def test_documents_routing_modes(self):
        doc = (DOCS_DIR / "ROUTING_ARCHITECTURE.md").read_text(encoding="utf-8")
        assert "Explicit Model" in doc
        assert "Auto Routing" in doc

    def test_documents_constraints(self):
        doc = (DOCS_DIR / "ROUTING_ARCHITECTURE.md").read_text(encoding="utf-8")
        assert "allowed_providers" in doc
        assert "blocked_models" in doc
