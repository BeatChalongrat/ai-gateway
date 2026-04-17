"""P8-10: Enterprise controls documentation tests."""

from pathlib import Path

DOCS_DIR = Path(__file__).resolve().parent.parent.parent / "docs"


class TestEnterpriseDocs:
    def test_doc_exists(self):
        assert (DOCS_DIR / "ENTERPRISE_CONTROLS.md").exists()

    def test_documents_rbac(self):
        doc = (DOCS_DIR / "ENTERPRISE_CONTROLS.md").read_text(encoding="utf-8")
        assert "RBAC" in doc
        assert "admin" in doc.lower()

    def test_documents_quota(self):
        doc = (DOCS_DIR / "ENTERPRISE_CONTROLS.md").read_text(encoding="utf-8")
        assert "Quota" in doc or "quota" in doc

    def test_documents_budget(self):
        doc = (DOCS_DIR / "ENTERPRISE_CONTROLS.md").read_text(encoding="utf-8")
        assert "Budget" in doc or "budget" in doc

    def test_documents_audit(self):
        doc = (DOCS_DIR / "ENTERPRISE_CONTROLS.md").read_text(encoding="utf-8")
        assert "audit" in doc.lower()
