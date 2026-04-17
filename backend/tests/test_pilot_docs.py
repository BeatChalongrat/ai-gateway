"""PR-07: Pilot-readiness documentation tests."""

from pathlib import Path

DOCS_DIR = Path(__file__).resolve().parent.parent.parent / "docs"


class TestPilotDocs:
    def test_doc_exists(self):
        assert (DOCS_DIR / "PILOT_READINESS.md").exists()

    def test_documents_all_blockers(self):
        doc = (DOCS_DIR / "PILOT_READINESS.md").read_text(encoding="utf-8")
        assert "tenant isolation" in doc.lower()
        assert "rate limit" in doc.lower()
        assert "secret" in doc.lower()
        assert "region" in doc.lower()
        assert "body size" in doc.lower() or "request size" in doc.lower()

    def test_documents_production_gaps(self):
        doc = (DOCS_DIR / "PILOT_READINESS.md").read_text(encoding="utf-8")
        assert "Still Blocks" in doc or "production" in doc.lower()
