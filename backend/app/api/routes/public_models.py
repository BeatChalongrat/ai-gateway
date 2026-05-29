from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db_session
from app.schemas.common import ErrorResponse
from app.schemas.public import ModelInfo, ModelListResponse, ReasoningCapability
from app.services.provider_key_service import ProviderKeyService
from app.services.providers.model_registry import get_all_candidates

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
        entries.append(
            ModelInfo(
                id=c.model,
                provider=c.provider,
                reasoning=reasoning_block,
            )
        )
    return ModelListResponse(data=entries)
