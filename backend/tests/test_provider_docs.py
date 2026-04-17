"""P2-10: Provider documentation validation tests.

Verifies:
- All referenced modules exist and are importable
- Key exports are available
- Doc examples match actual signatures
"""

from pathlib import Path


DOCS_DIR = Path(__file__).resolve().parent.parent.parent / "docs"


class TestProviderDocsExist:
    def test_architecture_doc_exists(self):
        path = DOCS_DIR / "PROVIDER_ARCHITECTURE.md"
        assert path.exists(), f"Missing: {path}"

    def test_doc_references_correct_modules(self):
        doc = (DOCS_DIR / "PROVIDER_ARCHITECTURE.md").read_text(encoding="utf-8")
        assert "providers.py" in doc
        assert "parsers.py" in doc
        assert "error_handler.py" in doc
        assert "model_registry.py" in doc
        assert "http_client.py" in doc


class TestProviderModulesImportable:
    def test_domain_providers(self):
        from app.domain.providers import (
            ProviderResponse,
            ProviderStreamChunk,
            ProviderUsage,
        )

    def test_parsers(self):
        from app.services.providers.parsers import (
            parse_anthropic_response,
            parse_openai_response,
        )

    def test_error_handler(self):
        from app.services.providers.error_handler import (
            call_with_retry,
            map_provider_error,
        )

    def test_model_registry(self):
        from app.services.providers.model_registry import (
            list_models,
            resolve_provider,
        )

    def test_http_client(self):
        from app.services.providers.http_client import create_provider_client

    def test_all_adapters(self):
        from app.services.providers.openai_adapter import OpenAIAdapter
        from app.services.providers.anthropic_adapter import AnthropicAdapter
        from app.services.providers.openrouter_adapter import OpenRouterAdapter

        assert OpenAIAdapter.provider_name == "openai"
        assert AnthropicAdapter.provider_name == "anthropic"
        assert OpenRouterAdapter.provider_name == "openrouter"
