"""Image-backend adapter contract + registry (FIX67 Part 1).

The video vertical dispatches through a ``(provider, job_type)`` registry so a
second backend is a registration, not a client edit. Image had none — it called
``openai_images_client`` directly behind ``IMAGE_GENERATION_PROVIDERS =
frozenset({"openai"})``. This gives image the same shape: a provider registers an
``ImageBackend`` here, and the destination order (``destinations.py``) decides
which registered backend runs, in what sequence. Adding a backend never edits a
frozenset.

An image call is synchronous (returns bytes now), unlike the async video job, so
this is a plain ``generate`` — not the submit/poll ABC. The shared piece with
video is the *order + rule*, not the runner (see ``destinations.py``).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class ImageGenResult:
    """What a backend produced, plus WHO produced it (for attribution)."""

    provider: str
    model: str
    data: list[dict]  # [{b64_json|url, revised_prompt?}]
    cost_usd: float = 0.0
    raw: dict = field(default_factory=dict)


class ImageBackend(ABC):
    """A single image-generation provider."""

    #: Stable provider key (matches ``model_configs.provider`` + the order).
    provider_name: str = ""

    @abstractmethod
    async def generate(
        self,
        session: AsyncSession,
        *,
        model: str,
        prompt: str,
        n: int = 1,
        size: str = "1024x1024",
        quality: str = "auto",
        style: str = "vivid",
        response_format: str = "b64_json",
        request_id: str | None = None,
    ) -> ImageGenResult:
        """Generate one or more images, or raise on failure.

        A raise is how the destination walker learns to try the next tier, so a
        backend MUST raise (not return an empty result) when it cannot produce.
        """
        raise NotImplementedError


# --- registry -------------------------------------------------------------
_IMAGE_BACKENDS: dict[str, ImageBackend] = {}


def register_image_backend(backend: ImageBackend) -> None:
    if not backend.provider_name:
        raise ValueError("image backend has no provider_name")
    _IMAGE_BACKENDS[backend.provider_name] = backend


def get_image_backend(provider: str) -> ImageBackend | None:
    return _IMAGE_BACKENDS.get(provider)


def registered_image_providers() -> set[str]:
    """Providers with a registered image backend — the replacement for the old
    hardcoded ``IMAGE_GENERATION_PROVIDERS`` frozenset."""
    return set(_IMAGE_BACKENDS)
