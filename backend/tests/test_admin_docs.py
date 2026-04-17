"""P7-10: Admin documentation validation tests."""

from pathlib import Path

DOCS_DIR = Path(__file__).resolve().parent.parent.parent / "docs"


class TestAdminDocs:
    def test_doc_exists(self):
        assert (DOCS_DIR / "ADMIN_API.md").exists()

    def test_documents_endpoints(self):
        doc = (DOCS_DIR / "ADMIN_API.md").read_text(encoding="utf-8")
        for endpoint in ["/admin/tenants", "/admin/projects", "/admin/api-keys",
                         "/admin/providers", "/admin/models", "/admin/requests",
                         "/admin/usage/summary"]:
            assert endpoint in doc

    def test_documents_pagination(self):
        doc = (DOCS_DIR / "ADMIN_API.md").read_text(encoding="utf-8")
        assert "limit" in doc
        assert "offset" in doc
        assert "total" in doc

    def test_documents_security_note(self):
        doc = (DOCS_DIR / "ADMIN_API.md").read_text(encoding="utf-8")
        assert '"key"' in doc or "plaintext key" in doc
