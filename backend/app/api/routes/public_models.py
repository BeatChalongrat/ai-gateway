from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db_session
from app.core.config import settings
from app.schemas.common import ErrorResponse
from app.schemas.public import (
    ModelCapabilities,
    ModelInfo,
    ModelListResponse,
    ReasoningCapability,
)
from app.services.provider_key_service import ProviderKeyService
from app.services.providers.model_registry import get_all_candidates
from app.services.providers.openai_images_client import IMAGE_MODELS

router = APIRouter()


@router.get(
    "/models",
    response_model=ModelListResponse,
    # Drop the optional ``reasoning`` block from the synthetic ``auto``
    # entry (and any candidate where the capability is absent) so old
    # clients see the pre-T1 wire shape unchanged.
    response_model_exclude_none=True,
    responses={500: {"model": ErrorResponse}},
)
async def get_models(
    session: Session = Depends(get_db_session),
) -> ModelListResponse:
    """List available models.

    Returns candidates from the registry plus the ``auto`` gateway
    pseudo-model. Each registry candidate is enriched with a
    ``reasoning`` capability block when ``supports_reasoning=True``
    so clients can discover which models accept the public
    ``ReasoningConfig`` knob without consulting admin endpoints.

    Filtered to providers that have at least one validated key: a model
    whose provider has no usable key can't be called, so listing it
    would only mislead the UI. The synthetic ``auto`` entry is always
    included (the gateway router resolves it to whatever IS reachable).
    """
    valid_providers = ProviderKeyService().providers_with_valid_keys(session)
    entries: list[ModelInfo] = [ModelInfo(id="auto", provider="gateway")]
    for c in get_all_candidates():
        if c.provider not in valid_providers:
            continue
        reasoning_block: ReasoningCapability | None = None
        if c.supports_reasoning:
            # ``modes`` is the same closed set as ``ReasoningConfig.mode``
            # — kept hardcoded here rather than imported from the
            # Literal so the wire contract stays explicit.
            reasoning_block = ReasoningCapability(
                supported=True,
                modes=["off", "auto", "low", "medium", "high"],
                default_mode=c.default_reasoning_mode or "auto",
                summary_supported=True,
            )
        # Capability flags for client model-selectors. Only TRUE flags are
        # set (None ones are dropped from the wire). image_generation_via_tools
        # is derived: ANY tool-capable chat model can emit images by calling the
        # generate_image tool, which routes to the image model (gpt-image-1)
        # regardless of the chat model's own provider — so this is gated on tool
        # support, not provider.
        cap_flags: dict[str, bool] = {}
        if c.supports_streaming:
            cap_flags["streaming"] = True
        if c.supports_vision:
            cap_flags["vision"] = True
        if c.supports_tools:
            cap_flags["tools"] = True
        if c.supports_reasoning:
            cap_flags["reasoning"] = True
        if c.supports_tools:
            cap_flags["image_generation_via_tools"] = True
        # Video generation via the generate_video tool — same gate as
        # image_generation_via_tools (any tool-capable chat model can call
        # it), additionally behind the video_gen feature flag so the 🎬
        # affordance only lights up where the feature is enabled.
        if c.supports_tools and settings.video_gen_enabled:
            cap_flags["video_generation"] = True
        entries.append(
            ModelInfo(
                id=c.model,
                provider=c.provider,
                reasoning=reasoning_block,
                capabilities=ModelCapabilities(**cap_flags) if cap_flags else None,
            )
        )

    # Image models (DALL-E) — decoupled from the chat routing candidate pool
    # so ``model:auto`` never sends a chat request to an image model. Listed
    # only when OpenAI has a usable key (same gate as chat models).
    if "openai" in valid_providers:
        for m in IMAGE_MODELS:
            entries.append(
                ModelInfo(
                    id=m,
                    provider="openai",
                    capabilities=ModelCapabilities(image_generation=True),
                )
            )

    return ModelListResponse(data=entries)
