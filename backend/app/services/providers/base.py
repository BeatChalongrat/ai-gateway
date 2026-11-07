from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, AsyncIterator

from app.domain.providers import ProviderResponse, ProviderStreamChunk

if TYPE_CHECKING:
    from app.schemas.common import Message


class BaseProviderAdapter(ABC):
    """Abstract base for all provider adapters.

    Each adapter translates between the gateway's canonical format and
    the upstream provider's API, returning a ``ProviderResponse``.
    """

    provider_name: str
    supports_streaming: bool = False

    @abstractmethod
    async def chat(
        self, payload, messages: list[Message], *, resolved_model: str | None = None
    ) -> ProviderResponse:
        """Send messages to the provider and return a canonical response.

        ``resolved_model`` is the concrete model name chosen by the routing
        engine.  Adapters should use this instead of ``payload.model``.
        """
        raise NotImplementedError

    async def stream_chat(
        self, payload, messages: list[Message], *, resolved_model: str | None = None
    ) -> AsyncIterator[ProviderStreamChunk]:
        """Stream messages from the provider as chunks.

        Override in adapters that support streaming.
        Default raises NotImplementedError.
        """
        raise NotImplementedError(
            f"{self.provider_name} adapter does not support streaming"
        )
        # yield is needed for the type checker to recognize this as an async generator
        yield  # pragma: no cover
