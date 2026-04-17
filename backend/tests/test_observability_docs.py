"""P6-10: Observability documentation validation tests."""

from pathlib import Path

DOCS_DIR = Path(__file__).resolve().parent.parent.parent / "docs"


class TestObservabilityDocs:
    def test_doc_exists(self):
        assert (DOCS_DIR / "OBSERVABILITY.md").exists()

    def test_documents_lifecycle_events(self):
        doc = (DOCS_DIR / "OBSERVABILITY.md").read_text(encoding="utf-8")
        for event in ["request_start", "request_complete", "dlp_scan_complete", "route_selected"]:
            assert event in doc

    def test_documents_privacy(self):
        doc = (DOCS_DIR / "OBSERVABILITY.md").read_text(encoding="utf-8")
        assert "Privacy" in doc or "privacy" in doc

    def test_documents_usage_tracking(self):
        doc = (DOCS_DIR / "OBSERVABILITY.md").read_text(encoding="utf-8")
        assert "UsageRecord" in doc
        assert "is_estimate" in doc
