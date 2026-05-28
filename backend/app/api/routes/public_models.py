from fastapi import APIRouter

from app.schemas.common import ErrorResponse
from app.schemas.public import ModelInfo, ModelListResponse, ReasoningCapability
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
async def get_models() -> ModelListResponse:
    """List available models.

    Returns candidates from the registry plus the ``auto`` gateway
    pseudo-model. Each registry candidate is enriched with a
    ``reasoning`` capability block when ``supports_reasoning=True``
    so clients can discover which models accept the public
    ``ReasoningConfig`` knob without consulting admin endpoints.
    """
    entries: list[ModelInfo] = [ModelInfo(id="auto", provider="gateway")]
    for c in get_all_candidates():
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
