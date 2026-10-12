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
from app.services.auto_router.tiers import band_for_candidate as _band_for_candidate
from app.services.media_gen import destinations as media_dest
from app.services.media_gen.image_backends import registered_image_providers
from app.services.providers.base_urls import SELF_HOSTED_PROVIDERS
from app.services.providers.recipients import keys_of as recipient_keys
from app.services.providers.model_registry import get_all_candidates
from app.services.providers.reasoning_strategies import (
    STRATEGIES_WITH_ENABLE_THINKING_OFF,
    STRATEGY_GEMINI_THINKING,
)

def _reasoning_can_turn_off(candidate) -> bool:
    """FIX125 M3 — whether ``reasoning.mode == "off"`` really stops thinking.

    * ``openai_compatible_enable_thinking`` (Qwen/vLLM): the adapter sends
      ``chat_template_kwargs.enable_thinking=false`` — measured (FIX72).
    * Anthropic: thinking exists only when the adapter sends a
      ``thinking`` block, and it sends none on "off".
    * Gemini (``gemini_thinking_level``): left as it was — "off" omits
      ``thinkingConfig``, and whether the model then thinks is the model's
      own default, which is not recorded per row.

    Everything else (DeepSeek / Groq ``openai_compatible_reasoning``, the
    OpenAI Responses rows, parse-only ``openai_compatible_reasoning_content``
    boxes, OpenRouter) thinks anyway when "off" is sent: the adapter only
    stops asking for more.
    """
    strategy = getattr(candidate, "reasoning_strategy", None) or "none"
    if strategy in STRATEGIES_WITH_ENABLE_THINKING_OFF:
        return True
    if candidate.provider == "anthropic":
        return True
    if strategy == STRATEGY_GEMINI_THINKING or candidate.provider == "google":
        return True
    return False


router = APIRouter()


def usable_image_destinations(session, valid_providers):
    """The image destinations that can produce right now, in order (≤2 tiers).

    FIX67 — replaces the ``IMAGE_GENERATION_PROVIDERS`` frozenset: the operator's
    ``media_order:image`` decides the backends, filtered to those with a usable
    key (or ``mock`` when explicitly enabled) and a registered backend. Adding a
    provider is a row + an adapter + an order entry, no code here."""
    order = media_dest.resolve_order_sync(session, "image")
    if not order:
        # Legacy default: a lone openai destination (pre-FIX67 behaviour) so a
        # deployment that never seeds the order still offers image generation.
        order = [media_dest.Destination(provider="openai", model="gpt-image-1")]
    return media_dest.usable_destinations(
        order,
        valid_providers=valid_providers,
        mock_enabled=media_dest.allow_mock_sync(session),
        registered_providers=registered_image_providers(),
    )


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
    # FIX67 — "is there a model that can make an image right now" is now "does
    # the media_order:image have a usable destination" (a real key, or mock when
    # enabled). One answer, so the via-tools flag and the image-model listing
    # cannot disagree, and the 🎨 button reflects the whole ordered pipeline
    # rather than a single hardcoded provider.
    image_destinations = usable_image_destinations(session, valid_providers)
    image_target_available = len(image_destinations) > 0
    image_provider_set = {d.provider for d in image_destinations}
    entries: list[ModelInfo] = [ModelInfo(id="auto", provider="gateway")]
    # FIX28 §5.2: a video model (supports_video_gen) is listed ONCE, by the
    # media loop below with video_generation set — never as a chat entry.
    # Before this, sora-2 rode the chat listing with only "streaming" and the
    # clients put it in the chat picker.
    from app.models.model_config import ModelConfigORM as _VideoRowORM
    from sqlalchemy import select as _vsel
    video_model_ids = {
        row.model for row in session.execute(
            _vsel(_VideoRowORM).where(_VideoRowORM.supports_video_gen.is_(True))
        ).scalars().all()
    }
    for c in get_all_candidates():
        if c.provider not in valid_providers:
            continue
        if c.model in video_model_ids:
            continue
        reasoning_block: ReasoningCapability | None = None
        if c.supports_reasoning:
            # ``modes`` is the same closed set as ``ReasoningConfig.mode``
            # — kept hardcoded here rather than imported from the
            # Literal so the wire contract stays explicit.
            # FIX125 M3 — "off" is listed only where the adapter can make
            # it true (``_reasoning_can_turn_off``); elsewhere an "off"
            # request still thinks, so offering it was a lie.
            _modes = ["auto", "low", "medium", "high"]
            if _reasoning_can_turn_off(c):
                _modes.insert(0, "off")
            reasoning_block = ReasoningCapability(
                supported=True,
                modes=_modes,
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
        # FIX62 — "can call tools" is not "can make images". The
        # generate_image tool needs a model at the other end: image
        # dispatch goes to the OpenAI images endpoint, so it is usable
        # only while OpenAI has a usable key — the same gate the dedicated
        # image models are listed under below. Both clients' 🎨 button
        # reads this flag, so it goes dark everywhere when the target is
        # missing, exactly like the 🎬 rule (FIX11 §1).
        if c.supports_tools and image_target_available:
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
        # FIX28 §5.2: a model's KIND is a catalog fact, not a deployment
        # setting. Advertise video_generation from the flag alone so the
        # clients classify sora-2 as a video model (hidden from the chat
        # picker) whether or not video is enabled — the gate lives in
        # /v1/video/config and the submit path, not in the model kind.
        if getattr(c, "supports_video_gen", False):
            cap_flags["video_generation"] = True
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
                # FIX35 Part C — price band for the picker badge / tier ceiling.
                cost_tier=_band_for_candidate(c),
                show_in_chat=c.show_in_chat,
                # FIX65 — the picker groups the operator's own box apart
                # from the curated vendor list (None keeps it off the wire).
                self_hosted=True if c.provider in SELF_HOSTED_PROVIDERS else None,
                recipients=recipient_keys(c.provider, c.model) or None,
            )
        )

    # Image models — catalogue rows (supports_image_gen), decoupled from the
    # chat routing candidate pool (they are is_active=false so ``model:auto``
    # never elects them). FIX67: listed from the catalogue, not a Python tuple,
    # and only for a provider that is a usable image destination right now — so
    # a new image backend appears here by adding a row + an order entry.
    from app.models.model_config import ModelConfigORM as _ImgRowORM
    from sqlalchemy import select as _isel

    if image_target_available:
        img_rows = session.execute(
            _isel(_ImgRowORM).where(_ImgRowORM.supports_image_gen.is_(True))
        ).scalars().all()
        for mc in img_rows:
            if mc.provider not in image_provider_set:
                continue
            entries.append(
                ModelInfo(
                    id=mc.model,
                    provider=mc.provider,
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
    # FIX84 Part B — the ``mock`` provider is a test backend. The admin switch
    # ``media_allow_mock`` (default off) already refuses it as a generation
    # DESTINATION (destinations.usable_destinations), and the image listing
    # above honours that. This listing did not, so "Mock Video 1 · mock" sat
    # first in every real user's picker while the switch said no. Same
    # answer, same switch, no third axis: a mock row is offered only while
    # the operator has explicitly allowed mock.
    mock_enabled = media_dest.allow_mock_sync(session)
    for mc in media_rows:
        if media_dest.is_mock_provider(mc.provider) and not mock_enabled:
            continue
        # FIX133 E6b — a media model whose provider has no valid, active key is
        # not offered (the chat listing above already gates on the same set).
        # Live: sora-2 / sora-2-pro stayed in the video picker after the OpenAI
        # key was turned off; a generation would have failed at submit.
        if not media_dest.is_mock_provider(mc.provider) and mc.provider not in valid_providers:
            continue
        mcaps: dict[str, bool] = {}
        if mc.supports_video_gen:  # FIX28 §5.2: kind is a fact; the gate is elsewhere
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
