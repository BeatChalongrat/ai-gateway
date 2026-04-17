# Observability, Audit & Usage

## Overview

The gateway provides structured observability for request lifecycle, provider execution, DLP/policy enforcement, token usage, and error telemetry. All observability is privacy-safe by design.

## Lifecycle Events

Every request through `/v1/chat/completions` produces structured log events:

| Event | Level | When | Key Fields |
|-------|-------|------|------------|
| `request_start` | INFO | Request received | request_id, tenant_id, project_id, model, stream |
| `dlp_scan_complete` | INFO | After DLP scan | direction, action, findings count, dlp_ms |
| `dlp_finding` | INFO | Per detection match | detector, entity_type, category, severity, action |
| `request_blocked` | WARN | DLP blocks request | policy_profile, finding_count, tenant_id |
| `route_selected` | INFO | After routing | provider, model, constrained |
| `provider_complete` | INFO | After provider call | provider, model, finish_reason, provider_ms |
| `request_complete` | INFO | Request finished | status, policy_action, tokens, total_ms, provider_ms |
| `request_audit` | INFO | Audit record | provider, model, tokens, policy_action, findings |
| `gateway_error` | WARN | Domain error | error_code, status, path, method |
| `validation_error` | WARN | Invalid request | error_count, request_id |
| `unhandled_exception` | ERROR | Unexpected error | exc_type, path, method |
| `provider_fallback` | WARN | Fallback triggered | failed_provider, error, remaining |

## Privacy Safety

| Guarantee | Mechanism |
|-----------|-----------|
| No raw message content in logs | Only metadata, counts, IDs logged |
| No secrets in logs | DLPAuditEntry structurally excludes `matched_text` |
| No API keys in logs | Only key_prefix and key_hash stored |
| No provider responses in logs | Only token counts and finish_reason |

## Usage Tracking

### UsageRecord

```python
@dataclass(frozen=True)
class UsageRecord:
    request_id: str
    tenant_id: str
    project_id: str
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost_usd: float
    is_estimate: bool  # True = heuristic, False = from provider
```

### Cost Estimation

Per-model cost rates in `usage_service.py`:

| Model | Cost/Token (USD) |
|-------|-----------------|
| gpt-4o-mini | $0.00000015 |
| gpt-4o | $0.0000025 |
| claude-3-5-sonnet | $0.000003 |
| claude-3-opus | $0.000015 |
| claude-3-haiku | $0.00000025 |

When the provider returns real usage data, `is_estimate=False`. When using heuristic estimation (character count / 4), `is_estimate=True`.

## Persistence

### RequestLogORM

Persisted in `request_logs` table:

| Column | Type | Description |
|--------|------|-------------|
| request_id | PK | Request identifier |
| tenant_id | String (indexed) | Tenant |
| project_id | String (indexed) | Project |
| provider | String | Provider used |
| model | String | Model used |
| status | String | success, blocked, provider_error, etc. |
| policy_action | String | allow, mask, redact, block |
| latency_ms | Integer | End-to-end latency |
| prompt_tokens | Integer | Input tokens |
| completion_tokens | Integer | Output tokens |
| total_tokens | Integer | Total tokens |
| estimated_cost_usd | Float | Cost estimate |
| error_code | String (nullable) | Error code if failed |
| created_at | DateTime | Timestamp |

### Query API

`RequestLogService` provides:
- `list_recent(session, limit)` — recent requests
- `list_by_project(session, project_id, limit)` — project-specific
- `get_by_id(session, request_id)` — single request
- `usage_summary_by_project(session, project_id)` — aggregated usage

The admin endpoint `GET /admin/requests` returns real persisted data.

## Request Tracing

Every request is assigned an `X-Request-Id` (auto-generated or client-supplied). This ID appears in:
- All lifecycle log events
- Error response bodies
- Response headers
- Persisted request logs

## Module Map

```
app/
  domain/
    observability.py     RequestRecord, UsageRecord, RequestStatus

  services/
    audit_service.py     DLP audit + request_audit event emission
    usage_service.py     Token estimation + UsageRecord building
    request_log_service.py  Persistence + query layer

  models/
    request_log.py       RequestLogORM

  middleware/
    request_context.py   X-Request-Id propagation
    error_handler.py     Structured error telemetry
```

## Known Limitations

- Request log persistence is not yet wired in the main orchestrator pipeline (records must be written by the caller)
- No real-time dashboard or alerting
- No log sampling for high-volume deployments
- Cost estimates are static per-model, not live pricing
- Streaming requests don't yet persist completion records
