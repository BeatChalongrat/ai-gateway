# Admin API Reference

The admin API provides management and observability endpoints for gateway operators. All endpoints are under the `/admin` prefix.

**Note:** Admin authentication (RBAC) is deferred to Phase 9. Currently all admin endpoints are open.

## Tenants

### GET /admin/tenants
List all tenants with pagination.

**Query params:** `limit` (default 100), `offset` (default 0)

**Response:**
```json
{
  "items": [
    {"tenant_id": "...", "name": "Acme Corp", "slug": "acme", "status": "active", "region": "global", "created_at": "..."}
  ],
  "total": 1, "limit": 100, "offset": 0
}
```

### GET /admin/tenants/{tenant_id}
Get tenant detail.

### POST /admin/tenants
Create a new tenant.

**Body:** `{"name": "...", "slug": "...", "region": "global"}`

## Projects

### GET /admin/projects
List projects. Optional filter: `?tenant_id=...`

### GET /admin/projects/{project_id}
Get project detail.

### POST /admin/projects
Create a project. Validates tenant exists.

**Body:** `{"tenant_id": "...", "name": "...", "policy_profile_id": "default"}`

## API Keys

### GET /admin/api-keys
List API keys. Optional filter: `?project_id=...`

**Note:** Plaintext key values are never shown in list responses.

### GET /admin/api-keys/{key_id}
Get API key detail (plaintext key is not returned).

### POST /admin/api-keys
Create an API key. Returns the plaintext key **once** — it cannot be retrieved again.

**Body:** `{"tenant_id": "...", "project_id": "...", "name": "CI Key"}`

**Response includes `"key"`** (the full `gw_...` value) — save it immediately.

### DELETE /admin/api-keys/{key_id}
Revoke an API key (sets status to "revoked").

## Providers & Models

### GET /admin/providers
List providers with their registered models.

### GET /admin/models
List all models with capability metadata (streaming, cost tier, latency tier).

### GET /admin/policies
List policy profiles.

## Requests & Audit

### GET /admin/requests
List recent request logs. Optional filter: `?project_id=...`

### GET /admin/requests/{request_id}
Get request detail with full metadata: provider, model, status, policy action, token usage, latency, cost, error code.

**Privacy:** No raw message content is stored or returned.

## Usage

### GET /admin/usage/summary?project_id=...
Get aggregated usage for a project.

**Response:**
```json
{
  "project_id": "...",
  "total_requests": 42,
  "total_tokens": 50000,
  "total_estimated_cost_usd": 0.05
}
```

## Pagination

List endpoints support `limit` and `offset` query parameters. Responses include:
- `total` — total count of matching records
- `limit` — current page size
- `offset` — current page offset

## Error Responses

All admin endpoints use the unified error envelope:
```json
{"error": {"code": "not_found", "message": "..."}, "request_id": "..."}
```

## Known Limitations

- No admin authentication/RBAC (deferred to Phase 9)
- No tenant/project update or delete endpoints
- No policy profile CRUD via admin API (profiles are code-defined)
- Pagination only on tenants endpoint (others return all records up to limit)
- Frontend admin-ui exists as stub — backend API is the primary admin interface
