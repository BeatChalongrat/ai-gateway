# Pilot Readiness

## What Was Fixed

This fix pack addresses 5 audit-identified pilot blockers:

| # | Blocker | Fix | Status |
|---|---------|-----|--------|
| 1 | Admin endpoints had no tenant isolation | `check_tenant_access()` on detail/list endpoints | Fixed |
| 2 | Rate limiting was a no-op stub | In-memory sliding window limiter | Fixed |
| 3 | Insecure default secrets accepted at startup | `validate_secrets()` rejects `"change-me"` in non-dev envs | Fixed |
| 4 | Region constraints defined but not enforced | Region blocked providers merged into routing constraints | Fixed |
| 5 | No request body size limit | `BodySizeLimitMiddleware` (1 MB default) | Fixed |

## Configuration

### Secrets
```bash
# Required for non-dev environments:
JWT_SECRET=your-strong-random-secret
MASTER_ENCRYPTION_KEY=your-strong-random-key
APP_ENV=production  # triggers validation
```

### Rate Limiting
In-memory sliding window, default 60 requests/minute per key. Configure in code:
```python
RateLimitService(max_requests=60, window_seconds=60)
```

### Request Body Limit
Default: 1 MB. Configured in `main.py`:
```python
app.add_middleware(BodySizeLimitMiddleware, max_bytes=1 * 1024 * 1024)
```

### Regional Controls
Set tenant region on creation. EU tenants automatically have OpenRouter blocked:
```json
POST /admin/tenants
{"name": "EU Corp", "slug": "eu-corp", "region": "eu"}
```

### Tenant Isolation
- `project_admin` and `tenant_admin` roles can only access their own tenant's data
- Global `admin` role bypasses tenant isolation
- Cross-tenant access returns 403

## What Still Blocks Full Production

| Area | Status | What's Needed |
|------|--------|---------------|
| Rate limiting | In-memory (pilot) | Redis-backed for multi-process |
| Quota/budget enforcement | Service exists, not wired in pipeline | Wire into orchestrator pre-dispatch |
| Admin auth | Working but API keys only | SSO/SAML for enterprise |
| Audit immutability | Hash chain available | DB-level write protection |
| Background jobs | In-process queue | Celery/ARQ for reliability |
| Webhook delivery | In-request | Move to background queue |
| Retention cleanup | Manual trigger | Cron/scheduler |
| Secrets management | Environment variables | Vault/KMS integration |
| Horizontal scaling | Single process | Load balancer + shared state |

## Pilot Readiness Assessment

The system is now **reasonable for a controlled pilot** with:
- Real multi-tenant isolation
- Active rate limiting
- Startup safety checks
- Region-aware routing
- Request size protection
- 1090+ passing tests

Pilot operators should:
1. Set strong secrets (`JWT_SECRET`, `MASTER_ENCRYPTION_KEY`)
2. Set `APP_ENV=production` (or `staging`)
3. Monitor structured logs for `alert_emitted`, `gateway_error`, `request_blocked` events
4. Set `budget_limit` and `max_requests_per_day` on projects as needed
5. Use `admin` role keys for admin operations, `user` role keys for API consumers
