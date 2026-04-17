from datetime import datetime
from typing import Generic, List, Optional, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class AdminListResponse(BaseModel, Generic[T]):
    """Paginated list envelope for admin endpoints."""

    items: List[T]
    total: Optional[int] = None
    limit: Optional[int] = None
    offset: Optional[int] = None


# --- Existing admin summaries (providers, policies, requests) ---


class ProviderSummary(BaseModel):
    provider_name: str
    enabled: bool
    models: List[str]


class PolicyProfileSummary(BaseModel):
    profile_id: str
    name: str
    mode: str


class RequestLogSummary(BaseModel):
    request_id: str
    provider: str
    model: str
    status: str
    policy_action: str
    latency_ms: int


class ModelDetail(BaseModel):
    model: str
    provider: str
    supports_streaming: bool
    cost_tier: str
    latency_tier: str


class RequestLogDetail(BaseModel):
    request_id: str
    tenant_id: str
    project_id: str
    provider: str
    model: str
    status: str
    policy_action: str
    policy_profile_id: Optional[str] = None
    latency_ms: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    error_code: Optional[str] = None
    created_at: Optional[datetime] = None
    # DLP trace (migration 021). Four message snapshots + the parsed
    # findings list. ``dlp_findings`` is the JSON-decoded form of the
    # stored ``dlp_findings_json`` column — None when no PII was found
    # OR when the request predates this feature.
    input_raw: Optional[str] = None
    input_masked: Optional[str] = None
    output_raw: Optional[str] = None
    output_final: Optional[str] = None
    dlp_findings: Optional[List[dict]] = None


class UsageSummary(BaseModel):
    # Nullable when the endpoint is queried without a project_id —
    # aggregate totals across every project are returned in that case.
    project_id: Optional[str] = None
    total_requests: int
    total_tokens: int
    total_estimated_cost_usd: float


# --- Tenant CRUD ---


class TenantCreate(BaseModel):
    name: str
    slug: str
    region: str = "global"
    ner_enabled: bool = False


class TenantResponse(BaseModel):
    tenant_id: str
    name: str
    slug: str
    status: str
    region: str
    ner_enabled: bool = False
    created_at: Optional[datetime] = None


class TenantUpdate(BaseModel):
    name: Optional[str] = None
    region: Optional[str] = None
    status: Optional[str] = None  # 'active' | 'inactive'
    ner_enabled: Optional[bool] = None


# --- Project CRUD ---


class ProjectCreate(BaseModel):
    tenant_id: str
    name: str
    policy_profile_id: Optional[str] = None


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    policy_profile_id: Optional[str] = None
    status: Optional[str] = None


class ProjectResponse(BaseModel):
    project_id: str
    tenant_id: str
    name: str
    policy_profile_id: Optional[str] = None
    created_at: Optional[datetime] = None


# --- API Key CRUD ---


class ApiKeyCreate(BaseModel):
    tenant_id: str
    project_id: str
    name: str


class ApiKeyCreateResponse(BaseModel):
    """Returned only on creation — includes the full plaintext key shown once."""

    key_id: str
    key_prefix: str
    key: str
    tenant_id: str
    project_id: str
    name: str
    status: str
    created_at: Optional[datetime] = None


class ApiKeyResponse(BaseModel):
    """Standard response — no raw key."""

    key_id: str
    key_prefix: str
    tenant_id: str
    project_id: str
    name: str
    status: str
    created_at: Optional[datetime] = None


# --- Policy CRUD ---


class PolicyCreate(BaseModel):
    profile_id: str
    name: str
    action: str = "allow"  # "allow" | "mask" | "block"
    rules: List[dict] = []


class PolicyUpdate(BaseModel):
    name: Optional[str] = None
    action: Optional[str] = None
    rules: Optional[List[dict]] = None


class PolicyResponse(BaseModel):
    profile_id: str
    name: str
    action: str
    rules: List[dict] = []
    created_at: Optional[datetime] = None


# --- Alert rules ---


ALERT_METRICS = (
    # Legacy display-only metric names (kept for backward compatibility
    # with rules created before the evaluator existed).
    "error_rate",
    "cost_per_hour",
    "requests_per_minute",
    # Metrics the evaluator actively monitors — align 1:1 with AlertType.
    "dlp_block_spike",
    "provider_failure",
    "budget_threshold",
    "quota_exhausted",
)
ALERT_SCOPES = ("global", "tenant", "project")


class AlertRuleCreate(BaseModel):
    name: str
    metric: str  # validated in the route against ALERT_METRICS
    threshold: float
    window_minutes: int = 5
    scope: str = "global"
    scope_id: Optional[str] = None
    enabled: bool = True
    webhook_url: Optional[str] = None


class AlertRuleUpdate(BaseModel):
    name: Optional[str] = None
    metric: Optional[str] = None
    threshold: Optional[float] = None
    window_minutes: Optional[int] = None
    scope: Optional[str] = None
    scope_id: Optional[str] = None
    enabled: Optional[bool] = None
    webhook_url: Optional[str] = None


class AlertRuleResponse(BaseModel):
    id: str
    name: str
    metric: str
    threshold: float
    window_minutes: int
    scope: str
    scope_id: Optional[str] = None
    enabled: bool
    webhook_url: Optional[str] = None
    last_fired_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


class AlertEventResponse(BaseModel):
    id: str
    rule_id: Optional[str] = None
    alert_type: str
    severity: str
    message: str
    tenant_id: Optional[str] = None
    project_id: Optional[str] = None
    delivery_status: str
    fired_at: Optional[datetime] = None


# --- Keyword rules ---


KEYWORD_RULE_SCOPES = ("global", "tenant", "project")


class KeywordRuleCreate(BaseModel):
    name: str
    keywords: List[str]
    model_override: str
    priority: int = 0
    scope: str = "global"
    scope_id: Optional[str] = None
    enabled: bool = True


class KeywordRuleUpdate(BaseModel):
    name: Optional[str] = None
    keywords: Optional[List[str]] = None
    model_override: Optional[str] = None
    priority: Optional[int] = None
    scope: Optional[str] = None
    scope_id: Optional[str] = None
    enabled: Optional[bool] = None


class KeywordRuleResponse(BaseModel):
    id: str
    name: str
    keywords: List[str]
    model_override: str
    priority: int
    scope: str
    scope_id: Optional[str] = None
    enabled: bool
    created_at: Optional[datetime] = None


class RoutingStatsResponse(BaseModel):
    models: List[dict]
    routers: List[dict]


# --- AI Sensitive Data Reviewer ---


class AiReviewSettings(BaseModel):
    enabled: bool
    monitored_key_ids: Optional[List[str]] = None
    scan_limit: int
    last_scan_at: Optional[datetime] = None
    # Provider config (migration 023). ``review_api_key_set`` is a bool
    # guard — the encrypted key itself is NEVER returned by any endpoint.
    review_provider: Optional[str] = None
    review_model: Optional[str] = None
    review_api_key_set: bool = False
    review_endpoint: Optional[str] = None
    realtime_enabled: bool = False
    realtime_sample_rate: int = 10


class AiReviewSettingsPatch(BaseModel):
    enabled: Optional[bool] = None
    monitored_key_ids: Optional[List[str]] = None
    scan_limit: Optional[int] = None
    review_provider: Optional[str] = None
    review_model: Optional[str] = None
    # Payload-only: plaintext key. Stored encoded, never echoed back.
    # Pass ``null`` to clear the key (falls back to env var).
    review_api_key: Optional[str] = None
    review_endpoint: Optional[str] = None
    realtime_enabled: Optional[bool] = None
    realtime_sample_rate: Optional[int] = None


class AiReviewScanRun(BaseModel):
    scan_run_id: str
    trigger: str
    provider: str
    model: str
    requests_scanned: int
    findings_count: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost_usd: float
    status: str
    error_message: Optional[str] = None
    created_at: Optional[datetime] = None


class AiReviewScanRunSummary(BaseModel):
    total_runs: int
    total_cost_usd: float
    total_tokens: int
    manual_runs: int
    realtime_runs: int
    avg_cost_per_run: float
    last_run_at: Optional[datetime] = None
