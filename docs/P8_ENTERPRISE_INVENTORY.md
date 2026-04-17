# P8-01: Enterprise Controls Inventory & Gap Analysis

## Current State

### What Exists

| Control | Status | Quality |
|---------|--------|---------|
| API key auth on public endpoints | Implemented | Good — SHA-256, expiry, revoke |
| `AuthorizationError` exception | Defined | **Never raised** |
| `QuotaExceededError` exception | Defined | **Never raised** |
| `ProjectORM.budget_limit` field | DB column exists | **No enforcement** |
| DLP/policy enforcement | Implemented | Good — scan, mask, block |
| Policy profiles (default, strict) | Code-defined | No DB-backed CRUD |
| Usage/cost accounting | Persisted | Good — tokens, cost, per-project summary |
| Admin endpoints | DB-backed | **No auth — completely open** |
| Rate limiting | Stub only | No-op, always allows |
| Audit logging | DLP findings only | No admin action audit |

### Critical Gaps

| Gap | Area | Severity |
|-----|------|----------|
| **GAP-E1** | No RBAC model — no roles, no permissions | Critical |
| **GAP-E2** | Admin endpoints completely unauthenticated | Critical |
| **GAP-E3** | Budget limit field exists but no enforcement | High |
| **GAP-E4** | No quota/rate controls enforced | High |
| **GAP-E5** | No alerts/webhooks for operational events | Medium |
| **GAP-E6** | No policy versioning or assignment history | Medium |
| **GAP-E7** | No audit trail for sensitive admin actions | High |
| **GAP-E8** | AuthorizationError defined but never raised | Medium |

### Trust Boundary Analysis

| Boundary | Current Protection | Gap |
|----------|-------------------|-----|
| Public API → Gateway | Bearer token (ApiKey) | OK |
| Admin API → Gateway | **None** | GAP-E2 |
| Tenant A → Tenant B data | Weak (no admin scoping) | GAP-E1 |
| Budget limit → Provider dispatch | **None** | GAP-E3 |
| Sensitive actions → Audit trail | **None** | GAP-E7 |

## Execution Plan

| Task | Gaps Addressed |
|------|---------------|
| **P8-02** RBAC domain model | GAP-E1 |
| **P8-03** Admin auth enforcement | GAP-E2, GAP-E8 |
| **P8-04** Quota controls | GAP-E4 |
| **P8-05** Budget cap enforcement | GAP-E3 |
| **P8-06** Alerts/webhooks foundation | GAP-E5 |
| **P8-07** Policy versioning | GAP-E6 |
| **P8-08** Sensitive action audit | GAP-E7 |
| **P8-09** Integration tests | All |
| **P8-10** Docs | All |
