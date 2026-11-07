from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Tenant:
    tenant_id: str
    name: str
    slug: str = ""
    region: str = "global"
    status: str = "active"


@dataclass
class Project:
    project_id: str
    tenant_id: str
    name: str
    policy_profile_id: Optional[str] = None
    budget_limit: Optional[float] = None


@dataclass
class ProviderModel:
    provider_name: str
    model_name: str
    supports_tools: bool = False
    supports_streaming: bool = True
    risk_level: str = "medium"
    region: str = "global"


@dataclass(frozen=True)
class RequestContext:
    """Typed context propagated through the request pipeline."""

    tenant_id: str
    project_id: str
    project_name: str
    policy_profile_id: Optional[str] = None
    request_id: Optional[str] = None
    region: str = "global"
    # Resolved at context-build time from tenant.ner_enabled OR the
    # org_settings.ner_enabled default. The orchestrator consults this
    # to decide whether to run PresidioDetector — it is NOT consulted
    # by the detector itself, which is stateless.
    ner_enabled: bool = False
    # DLP conversation vault — when the caller supplies
    # ``metadata.conversation_id`` the orchestrator threads it through
    # so ``ConversationVault`` can carry the span_map across turns.
    # ``None`` means "no cross-turn carry-over" (current per-request
    # behaviour). Optional + last in the field list keeps every
    # existing positional ``RequestContext(...)`` call site working.
    conversation_id: Optional[str] = None
    # Portal user this request bills/attributes to — key owner first
    # (api_keys.portal_user_id), trusted X-User-ID second; None for
    # admin/tenant keys. Copied onto RequestRecord.user_id at log time
    # (migration 062).
    user_id: Optional[str] = None
    # FIX119 — the TURN id the caller sent as X-Request-Id (chat-edge's
    # run_id for a web turn, the desktop's run id for a desktop turn). One
    # turn makes several gateway requests (tool rounds, retries), each with
    # its own ``request_id``; this is the id that joins them, the id every
    # layer logs, and the id the person sees on a failed turn's card.
    # None when the caller sent nothing usable.
    turn_id: Optional[str] = None


@dataclass
class RouteDecision:
    provider_name: str
    model_name: str
    reasons: List[str] = field(default_factory=list)
    constrained_by_policy: bool = False
    # Layer-1 same-tier fallbacks computed by ``RoutingService``. Each
    # entry is ``(provider_name, model_name)``. Consumed by the
    # dispatcher's ``dispatch_with_fallback`` when the primary call
    # raises a fallback-eligible error. Empty list = no Layer-1
    # alternatives available; orchestrator can still hand off to the
    # Layer-2 ``FallbackOrchestrator`` (cross-provider tier chain).
    fallbacks: List[tuple] = field(default_factory=list)


