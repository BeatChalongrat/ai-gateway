from fastapi import APIRouter

from app.schemas.common import ErrorResponse
from app.schemas.public import ModelInfo, ModelListResponse
from app.services.providers.model_registry import list_models

router = APIRouter()


@router.get(
    "/models",
    response_model=ModelListResponse,
    responses={500: {"model": ErrorResponse}},
)
async def get_models() -> ModelListResponse:
    """List available models.

    Returns models from the model registry plus the ``auto`` gateway
    model which lets the gateway choose the best provider.
    """
    entries = [ModelInfo(id="auto", provider="gateway")]
    for m in list_models():
        entries.append(ModelInfo(id=m["model"], provider=m["provider"]))
    return ModelListResponse(data=entries)
