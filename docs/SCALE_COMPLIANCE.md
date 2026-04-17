# Scale & Compliance Extensions

## Overview

This document covers the scale, reliability, and compliance foundations available in the gateway.

## Caching

### Architecture
- `CacheBackend` abstract interface with `get`/`set`/`delete`/`clear`
- `InMemoryCache` — default TTL-aware implementation
- `CacheService` — application-level wrapper, pluggable backend

### Usage
```python
from app.services.cache_service import CacheService
cache = CacheService()
cache.set("key", value, ttl_seconds=300)
result = cache.get("key")
```

### Redis Upgrade Path
Implement `CacheBackend` with a Redis client and pass to `CacheService(backend=RedisCacheBackend())`.

## Background Tasks

### Architecture
- `BackgroundTaskRunner` — bounded async queue with error isolation
- Tasks enqueued as coroutine factories
- Failed tasks logged, don't block the queue

### Usage
```python
runner = BackgroundTaskRunner(max_queue=1000)
runner.enqueue(my_async_func, arg1, arg2)
await runner.process_all()
```

### Production Path
Replace with Celery/ARQ + Redis broker for distributed work.

## Webhook Delivery

### Features
- Configurable max retries (default 3)
- Exponential backoff between retries
- Delivery status tracking (pending/delivered/failed)
- Error capture and logging

### Usage
```python
svc = WebhookService(max_retries=3)
delivery = await svc.deliver("https://hook.example.com", {"event": "test"})
assert delivery.status == DeliveryStatus.DELIVERED
```

## Data Retention

### Configuration
| Table | Default Retention | Configurable |
|-------|------------------|--------------|
| `request_logs` | 90 days | Yes |
| `admin_audit_log` | 365 days | Yes |

### Running Cleanup
```python
svc = RetentionService(request_log_days=90, audit_log_days=365)
result = svc.run_all(session)
```

Schedule via background task runner or external cron.

## Audit Integrity

### Hash Chain
- Each audit entry can include a `_chain_hash` in its details JSON
- Hash chains previous entry's hash with current entry's data
- `AuditIntegrityService.verify_chain()` validates the chain

### Verification
```python
svc = AuditIntegrityService()
result = svc.verify_chain(session, limit=100)
assert result["valid"] is True
```

### Limitations
- Hash chain is optional (legacy entries without hashes pass verification)
- No database-level write protection (future: triggers or append-only tables)
- Not a legally binding tamper-proof audit — a practical technical primitive

## Regional Controls

### Region Policies
| Region | Constraints |
|--------|------------|
| `global` | No restrictions |
| `eu` | Blocks `openrouter` (third-party routing) |
| `us` | No restrictions |

### Integration
```python
from app.services.region_service import get_region_constraints
constraints = get_region_constraints("eu")
# constraints.blocked_providers = frozenset({"openrouter"})
```

Wire into routing via `RoutingRequest.allowed_providers` / `blocked_models`.

## Identity Provider Scaffolding

### Architecture
- `IdentityProvider` abstract interface with `resolve(credential) -> IdentityInfo`
- `IdentityInfo` — universal identity record (provider, subject_id, tenant, roles, groups)
- `ApiKeyIdentityProvider` — current implementation wrapper

### Adding SSO/SAML/OIDC
1. Implement `IdentityProvider` for your IdP
2. Register via `register_identity_provider()`
3. Update `deps.py` to check registered providers

### SCIM
Add a SCIM-compatible user/group sync endpoint that maps to `IdentityInfo` objects. The `groups` and `roles` fields on `IdentityInfo` are designed for this.

## Module Map

```
app/
  domain/
    identity.py          IdentityInfo, IdentityProvider, ApiKeyIdentityProvider

  services/
    cache_service.py     CacheBackend, InMemoryCache, CacheService
    background_tasks.py  BackgroundTaskRunner
    webhook_service.py   WebhookService, WebhookDelivery, DeliveryStatus
    retention_service.py RetentionService
    audit_integrity.py   AuditIntegrityService, hash chain verification
    region_service.py    RegionConstraints, get_region_constraints
```

## Known Limitations

- Cache is in-memory only (no Redis implementation yet)
- Background tasks are in-process (no distributed queue)
- Webhook delivery is in-request (should move to background queue)
- Retention cleanup must be triggered manually (no scheduler)
- Audit hash chain is opt-in, not enforced by database
- Regional controls are static policies, not dynamically configurable
- SSO/SAML/SCIM are scaffolded interfaces only — no real IdP integration
- No Kubernetes/cloud-native deployment manifests
