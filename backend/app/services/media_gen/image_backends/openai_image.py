"""OpenAI image generation and reference-edit adapter. Producing provider/model are retained; edit failure never becomes a text-only generation."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.media_gen.image_backends.base import ImageBackend, ImageGenResult
# Import the MODULE (not the function) so a test that patches
# ``openai_images_client.generate_image`` at its source reaches this call.
from app.services.providers import openai_images_client


class OpenAIImageBackend(ImageBackend):
    provider_name = "openai"

    async def edit(self, session, *, source_images, **kwargs) -> ImageGenResult:
        result = await openai_images_client.generate_image(
            session, source_images=source_images, **kwargs,
        )
        return ImageGenResult(provider="openai", model=result.model, data=result.data,
                              cost_usd=result.cost_usd, raw=result.raw)

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
