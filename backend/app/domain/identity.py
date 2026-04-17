"""Identity provider abstraction for SSO/SAML/SCIM scaffolding.

Defines clean extension points for enterprise identity integration.
Current implementation uses API key auth only. Future: plug in SAML,
OIDC, or SCIM providers by implementing the IdentityProvider interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class IdentityInfo:
    """Resolved identity from any auth provider."""

    provider: str  # "api_key", "saml", "oidc", "scim"
    subject_id: str  # unique identifier within the provider
    tenant_id: str
    email: Optional[str] = None
    display_name: Optional[str] = None
    roles: tuple[str, ...] = ("user",)
    groups: tuple[str, ...] = ()
    metadata: dict = field(default_factory=dict)


class IdentityProvider(ABC):
    """Abstract identity provider interface.

    Implementations resolve a credential (token, assertion, etc.) into
    an IdentityInfo. The gateway can chain multiple providers.
    """

    provider_name: str

    @abstractmethod
    async def resolve(self, credential: str) -> IdentityInfo | None:
        """Resolve a credential to identity info. Returns None if not handled."""
        ...


class ApiKeyIdentityProvider(IdentityProvider):
    """Current API key-based identity provider.

    Wraps the existing ResolvedApiKey into the IdentityInfo contract.
    """

    provider_name = "api_key"

    async def resolve(self, credential: str) -> IdentityInfo | None:
        # In practice, this delegates to ApiKeyService.verify_key
        # This class just defines the interface contract
        return None  # Real resolution happens in deps.py


# Extension point for future providers
_REGISTERED_PROVIDERS: list[IdentityProvider] = [
    ApiKeyIdentityProvider(),
]


def get_identity_providers() -> list[IdentityProvider]:
    """Return registered identity providers."""
    return list(_REGISTERED_PROVIDERS)


def register_identity_provider(provider: IdentityProvider) -> None:
    """Register a new identity provider."""
    _REGISTERED_PROVIDERS.append(provider)
