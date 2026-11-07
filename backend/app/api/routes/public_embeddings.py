"""Embeddings endpoint — POST /v1/embeddings (FIX 2).

The gateway now fronts embeddings so chat-edge stops calling OpenAI directly
(which bypassed DLP). Flow: auth -> load tenant -> DLP embeddings egress scan
(credential-block + per-tenant PII + audit) -> OpenAI embeddings -> return.

The text is deliberately NOT masked — a masked embedding of "<PERSON_1>"
is useless for retrieval — but the credential/secret class is always
withheld, and tenants with ``kb_pii_egress = "block"`` fail closed on any
identity PII. Same auth (service JWT / API key, with the trusted end-user
identity for web-chat traffic) as chat completions.

OpenAI-compatible response shape so the chat-edge client parses it the same
way it parsed OpenAI directly.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ResolvedApiKey, get_resolved_api_key
from app.core.config import settings
from app.core.db import get_async_db
from app.models.tenant import TenantORM
from app.services.dlp.egress_policy import scan_embedding_text
from app.services.embeddings.openai_embeddings_client import generate_embedding

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["public-embeddings"])


class EmbeddingRequest(BaseModel):
    input: str = Field(..., min_length=1, max_length=32_000)
    model: str | None = None


@router.post("/embeddings")
async def create_embedding(
    payload: EmbeddingRequest,
    auth: ResolvedApiKey = Depends(get_resolved_api_key),
    session: AsyncSession = Depends(get_async_db),
) -> dict:
    tenant = await session.get(TenantORM, auth.tenant_id)

    # DLP egress scan BEFORE the provider call. Raises RawEgressBlockedError
    # (403) on credential/secret content, or on identity PII when the
    # tenant's kb_pii_egress = "block". On block nothing is embedded.
    await scan_embedding_text(
        payload.input,
        session,
        tenant,
        project_id=str(getattr(auth, "project_id", "") or ""),
        user_id=getattr(auth, "portal_user_id", None),
    )

    vector = await generate_embedding(payload.input, model=payload.model)

    # Persist any raw-egress audit row written during the scan.
    try:
        await session.commit()
    except Exception as exc:  # noqa: BLE001 — embedding already produced
        logger.warning("embedding_audit_commit_failed", error=str(exc))

    return {
        "object": "list",
        "model": payload.model or settings.openai_embedding_model,
        "data": [
            {"object": "embedding", "index": 0, "embedding": vector},
        ],
    }
