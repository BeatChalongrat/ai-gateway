# Enterprise Controls

## Overview

Enterprise controls provide governance, access restriction, usage limits, and audit capabilities for the gateway.

## RBAC (Role-Based Access Control)

### Roles

| Role | Level | Permissions |
|------|-------|-------------|
| `viewer` | 0 | Read usage data |
| `user` | 1 | Make API requests + read usage |
| `project_admin` | 2 | Manage keys, projects + admin read |
| `tenant_admin` | 3 | Manage tenants, projects, keys + admin write |
| `admin` | 4 | Full access including policy management |

### How It Works

- Each API key has a `role` field (default: `user`)
- Public `/v1/*` endpoints require any valid API key
- Admin `/admin/*` endpoints require `project_admin` or higher
- Role is checked via `require_admin` dependency on the admin router

### API Key Role Assignment

Set role when creating an API key:
```json
POST /admin/api-keys
{"tenant_id": "...", "project_id": "...", "name": "Admin Key"}
```
Role defaults to `user`. To create admin keys, update the `role` field in the database directly (admin key creation API is a future enhancement).

## Quota Controls

### Request Quota

Projects can have a `max_requests_per_day` limit:
- Checked before each request in the quota service
- Counts today's requests from `request_logs`
- Raises `QuotaExceededError` (HTTP 429) when exceeded

### Budget Cap

Projects can have a `budget_limit` (USD):
- Checked against accumulated `estimated_cost_usd` from request logs
- Raises `QuotaExceededError` (HTTP 429) when exceeded
- Cost estimates are based on per-model token rates
- `is_estimate: true` in error details when using estimated costs

### Setting Limits

Set via database (admin API for quota/budget management is a future enhancement):
```sql
UPDATE projects SET max_requests_per_day = 1000, budget_limit = 10.00 WHERE project_id = '...';
```

## Alerts

### Alert Types

| Type | Severity | Trigger |
|------|----------|---------|
| `budget_threshold` | warning/critical | Usage approaches or exceeds budget |
| `quota_exhausted` | critical | Daily request quota hit |
| `provider_failure` | warning | Provider call failed |
| `dlp_block_spike` | warning | Elevated DLP blocks |
| `key_revoked` | info | API key revoked |
| `sensitive_action` | info | Admin action taken |

### Delivery

Currently: structured log events via structlog.
Future: webhook delivery, Slack, email integrations.

## Admin Audit Trail

### What's Tracked

All sensitive admin operations are recorded in `admin_audit_log`:

| Field | Description |
|-------|-------------|
| `actor_key_id` | Who performed the action |
| `tenant_id` | Tenant scope |
| `action` | Operation name (e.g. `key_create`, `key_revoke`) |
| `resource_type` | Entity type (e.g. `api_key`, `project`) |
| `resource_id` | Affected resource ID |
| `details` | JSON context (e.g. old/new values) |
| `timestamp` | When it happened |

### Audited Actions

- `key_create` — new API key created
- `key_revoke` — API key revoked

### Query API

```python
AdminAuditService().list_by_tenant(session, tenant_id)
AdminAuditService().list_by_action(session, "key_revoke")
```

## Security Considerations

- Raw API keys are shown only once (on creation) — never stored or logged
- Admin audit entries use `actor_key_id`, not raw key values
- Budget/quota errors include `is_estimate: true` when costs are heuristic
- DLP audit entries structurally exclude matched text

## Known Limitations

- Role assignment requires direct DB access (no admin API yet)
- Quota/budget limits require direct DB access to set
- No real-time rate limiting (per-second/per-minute) — only daily quotas
- Alerts are log-based only (no webhook delivery yet)
- No policy versioning UI (admin audit log tracks changes)
- No SSO/SAML/SCIM integration
- Audit log has no retention/archival policy
