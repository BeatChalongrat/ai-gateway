"""Web-search endpoint — POST /v1/search + GET /v1/search/config.

Flow: auth -> tenant search-enabled gate -> fallback-chain search ->
flat per-query credit deduction -> return normalized results. Same auth
(API key, with the trusted X-User-ID end-user for web-chat traffic) and
credit-billing identity as chat completions, so a search drains the
actual chatting user's balance.

GET /v1/search/config lets chat-edge decide whether to even offer the
web_search tool to the model (tenant search on + at least one provider).
"""

import hashlib

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ResolvedApiKey, get_resolved_api_key
from app.core.db import get_async_db
from app.core.exceptions import (
    SearchDisabledError,
    SearchProvidersUnavailableError,
)
from app.models.search_provider import SearchProviderORM
from app.models.tenant import TenantORM
from app.services.dlp.egress_policy import scan_search_query
from app.services.gateway.portal_credit_check import deduct_portal_credit_async
from app.services.search.base import (
    SearchNotConfiguredError,
    SearchUnavailableError,
)
from app.services.search.service import search_service

logger = structlog.get_logger(__name__)
router = APIRouter()


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)
    max_results: int = Field(5, ge=1, le=10)


async def _load_tenant(session: AsyncSession, tenant_id: str) -> TenantORM | None:
    return await session.get(TenantORM, tenant_id)


@router.post("/search")
async def search(
    payload: SearchRequest,
    auth: ResolvedApiKey = Depends(get_resolved_api_key),
    session: AsyncSession = Depends(get_async_db),
) -> dict:
    tenant = await _load_tenant(session, auth.tenant_id)
    if tenant is None or not bool(getattr(tenant, "search_enabled", False)):
        raise SearchDisabledError("Web search is not enabled for this tenant.")

    # Phase 9 (A1): DLP egress scan BEFORE any provider call or credit
    # deduction. The query is intentionally NOT masked — search needs
    # the real text for useful results — but the credential/secret
    # class is always withheld, and tenants with search_pii_egress =
    # "block" fail closed on any identity PII. Raises 403
    # search_query_blocked; on block nothing is searched or billed.
    await scan_search_query(
        payload.query,
        session,
        tenant,
        project_id=str(getattr(auth, "project_id", "") or ""),
        user_id=getattr(auth, "log_user_id", None),
    )

    try:
        results = await search_service.search(
            session, auth.tenant_id, payload.query
        )
    except SearchNotConfiguredError as e:
        raise SearchProvidersUnavailableError(str(e)) from e
    except SearchUnavailableError as e:
        # All providers failed — the tool surfaces a graceful fallback to
        # the model. 503 so the caller can distinguish from "not enabled".
        # Commit FIRST so the per-provider health/failure stats recorded
        # during the chain survive (raising triggers the request-session
        # rollback otherwise).
        try:
            await session.commit()
        except Exception:  # noqa: BLE001
            pass
        raise SearchProvidersUnavailableError(str(e)) from e

    # Flat per-query credit charge (search bills per query, not by token
    # cost). Debits the trusted end-user behind the shared web-chat key.
    # Phase 9 (A2): the ledger reference/description must carry NO query
    # text — the query is PII-bearing and the billing row only needs a
    # correlation handle, so a truncated sha256 stands in for it.
    cost = int(getattr(tenant, "search_credit_cost", 5) or 5)
    query_hash = hashlib.sha256(payload.query.encode("utf-8")).hexdigest()[:12]
    try:
        await deduct_portal_credit_async(
            session,
            str(auth.key_id),
            f"search:{query_hash}",
            credits_override=cost,
            tenant_id=auth.tenant_id,
            portal_user_id=getattr(auth, "portal_user_id", None),
            description="web_search",
        )
        await session.commit()
    except Exception as exc:  # noqa: BLE001 — search already ran; don't fail it
        logger.warning("search_credit_deduct_failed", error=str(exc))

    logger.info(
        "search_request",
        tenant_id=auth.tenant_id,
        provider=results.provider,
        results_count=len(results.results),
        credits=cost,
    )
    return results.to_dict()


@router.get("/search/config")
async def search_config(
    auth: ResolvedApiKey = Depends(get_resolved_api_key),
    session: AsyncSession = Depends(get_async_db),
) -> dict:
    """Whether the caller's tenant should be offered the web_search tool:
    search enabled AND at least one enabled provider configured."""
    tenant = await _load_tenant(session, auth.tenant_id)
    enabled = bool(tenant is not None and getattr(tenant, "search_enabled", False))
    provider_count = 0
    if enabled:
        provider_count = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(SearchProviderORM)
                    .where(
                        SearchProviderORM.tenant_id == auth.tenant_id,
                        SearchProviderORM.is_enabled.is_(True),
                    )
                )
            ).scalar()
            or 0
        )
    return {
        "enabled": enabled and provider_count > 0,
        "search_enabled": enabled,
        "provider_count": provider_count,
    }
