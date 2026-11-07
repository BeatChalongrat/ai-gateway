"""OpenAI image backend — a thin wrapper over the existing images client.

This changes NOTHING about how ``gpt-image-1`` is produced: it calls the same
``openai_images_client.generate_image`` with the same arguments and re-raises the
same domain exceptions. The only new thing is the ``ImageGenResult`` envelope
carrying the producing provider/model so the caller can attribute and, on a
raise, fall to the next destination. (FIX67 tripwire: the gpt-image-1 path is
byte-for-byte unchanged.)
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.media_gen.image_backends.base import ImageBackend, ImageGenResult
# Import the MODULE (not the function) so a test that patches
# ``openai_images_client.generate_image`` at its source reaches this call.
from app.services.providers import openai_images_client


class OpenAIImageBackend(ImageBackend):
    provider_name = "openai"

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
        result = await openai_images_client.generate_image(
            session,
            model=model,
            prompt=prompt,
            request_id=request_id,
            n=n,
            size=size,
            quality=quality,
            style=style,
            response_format=response_format,
        )
        return ImageGenResult(
            provider="openai",
            model=result.model,
            data=result.data,
            cost_usd=getattr(result, "cost_usd", 0.0),
            raw=getattr(result, "raw", {}) or {},
        )
