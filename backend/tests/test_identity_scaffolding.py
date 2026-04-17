"""P10-08: SSO/SAML/SCIM scaffolding tests."""

import pytest

from app.domain.identity import (
    ApiKeyIdentityProvider,
    IdentityInfo,
    IdentityProvider,
    get_identity_providers,
)


class TestIdentityInfo:
    def test_construction(self):
        info = IdentityInfo(
            provider="saml",
            subject_id="user@corp.com",
            tenant_id="t1",
            email="user@corp.com",
            roles=("admin",),
        )
        assert info.provider == "saml"
        assert info.tenant_id == "t1"
        assert "admin" in info.roles

    def test_defaults(self):
        info = IdentityInfo(provider="api_key", subject_id="k1", tenant_id="t1")
        assert info.roles == ("user",)
        assert info.groups == ()
        assert info.email is None

    def test_frozen(self):
        info = IdentityInfo(provider="api_key", subject_id="k1", tenant_id="t1")
        with pytest.raises(AttributeError):
            info.provider = "x"  # type: ignore[misc]


class TestIdentityProvider:
    def test_api_key_provider_exists(self):
        providers = get_identity_providers()
        names = [p.provider_name for p in providers]
        assert "api_key" in names

    def test_api_key_provider_is_abstract_impl(self):
        provider = ApiKeyIdentityProvider()
        assert isinstance(provider, IdentityProvider)

    @pytest.mark.asyncio
    async def test_api_key_resolve_returns_none(self):
        """Base implementation returns None — real resolution is in deps.py."""
        provider = ApiKeyIdentityProvider()
        result = await provider.resolve("some-token")
        assert result is None

    def test_provider_interface_abstract(self):
        assert hasattr(IdentityProvider, "resolve")
