"""P10-10: Scale/compliance documentation tests."""

from pathlib import Path

DOCS_DIR = Path(__file__).resolve().parent.parent.parent / "docs"


class TestScaleDocs:
    def test_doc_exists(self):
        assert (DOCS_DIR / "SCALE_COMPLIANCE.md").exists()

    def test_documents_key_topics(self):
        doc = (DOCS_DIR / "SCALE_COMPLIANCE.md").read_text(encoding="utf-8")
        for topic in ["Caching", "Background", "Webhook", "Retention",
                       "Audit Integrity", "Regional", "Identity"]:
            assert topic in doc, f"Missing topic: {topic}"

    def test_documents_limitations(self):
        doc = (DOCS_DIR / "SCALE_COMPLIANCE.md").read_text(encoding="utf-8")
        assert "Known Limitations" in doc
