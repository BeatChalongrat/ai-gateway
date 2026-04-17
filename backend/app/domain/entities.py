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


@dataclass
class RouteDecision:
    provider_name: str
    model_name: str
    reasons: List[str] = field(default_factory=list)
    constrained_by_policy: bool = False


