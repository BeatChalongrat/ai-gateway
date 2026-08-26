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
        # Video generation via the generate_video tool. FIX11 §1: the 🎬
        # affordance must light up ONLY when video is genuinely usable, not on
        # the bare feature flag — a key being present is not a key being usable,
        # and our seeded model labels itself UNVERIFIED. So this mirrors the
        # capability answer in GET /v1/video/config: the gate AND a configured
        # key AND an owner-verified model. Both clients read this flag, so the
        # button stays visibly unavailable everywhere until all three agree.
        if (
            c.supports_tools
            and settings.video_gen_enabled
            and bool((settings.replicate_api_token or "").strip())
            and settings.video_model_verified
        ):
            cap_flags["video_generation_via_tools"] = True
        # Dedicated media-model capabilities (Sprint 2A.5). Chat candidates
        # don't carry these attributes, so getattr → False (no-op here). They
        # only matter once dedicated media models are surfaced to /v1/models
        # (see Sprint 2A.5 report: that listing is a follow-up gap).
        if getattr(c, "supports_audio_gen", False):
            cap_flags["audio_generation"] = True
        if getattr(c, "supports_tts", False):
            cap_flags["tts"] = True
        # Display data (models-catalog step 1): context window + per-1M
        # prices straight from the ``model_configs`` row, emitted only
        # when the value is a real positive number — zero/unknown stays
        # off the wire so chat-edge's curated fallback fills the gap
        # instead of the UI showing "free" or a zero-token window.
        ctx = c.max_context_tokens if (c.max_context_tokens or 0) > 0 else None
        in_price = (
            round(c.cost_per_input_token * 1_000_000, 4)
            if (c.cost_per_input_token or 0) > 0
            else None
        )
        out_price = (
            round(c.cost_per_output_token * 1_000_000, 4)
            if (c.cost_per_output_token or 0) > 0
            else None
        )
        entries.append(
            ModelInfo(
                id=c.model,
                provider=c.provider,
                reasoning=reasoning_block,
                capabilities=ModelCapabilities(**cap_flags) if cap_flags else None,
                context_window=ctx,
                input_price_per_1m=in_price,
                output_price_per_1m=out_price,
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

    # Dedicated media models (Sprint 2A.5) — direct-dispatch targets, NOT chat
    # candidates. Listed with their DEDICATED capability so chat-edge's
    # deriveModelKind sets model_kind=video/audio/tts and the UI shows the gen
    # form. Not provider-key gated (the mock model needs no key); video gated
    # on the feature flag. ``from sqlalchemy import select`` is local to keep
    # the import surface minimal.
    from sqlalchemy import or_, select as _select
    from app.models.model_config import ModelConfigORM as _ModelConfigORM

    media_rows = session.execute(
        _select(_ModelConfigORM).where(
            _ModelConfigORM.is_active.is_(True),
            or_(
                _ModelConfigORM.supports_video_gen.is_(True),
                _ModelConfigORM.supports_audio_gen.is_(True),
                _ModelConfigORM.supports_tts.is_(True),
            ),
        )
    ).scalars().all()
    for mc in media_rows:
        mcaps: dict[str, bool] = {}
        if mc.supports_video_gen and settings.video_gen_enabled:
            mcaps["video_generation"] = True
        if mc.supports_audio_gen:
            mcaps["audio_generation"] = True
        if mc.supports_tts:
            mcaps["tts"] = True
        if not mcaps:
            continue
        entries.append(
            ModelInfo(
                id=mc.model,
                provider=mc.provider,
                capabilities=ModelCapabilities(**mcaps),
            )
        )

    return ModelListResponse(data=entries)
