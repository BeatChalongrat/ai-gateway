# FE-01: Frontend UI Audit + Gap Analysis

## Current State

### What Exists
| Component | Status | Quality |
|-----------|--------|---------|
| Tailwind v4 + SRAN theme tokens | Installed | Good foundation |
| Sidebar with navigation | Working | Needs icon upgrade, polish |
| AppShell layout | Working | Needs refinement |
| Dashboard page | Uses design system | Static data, needs real backend |
| UI primitives (Badge, StatCard, etc.) | Created | Good, need expansion |
| API client (`lib/api.ts`) | Basic fetch wrapper | Needs error handling, POST/DELETE |
| 6 feature pages | **Placeholder stubs** | All are `<p>TODO</p>` |

### Page-by-Page Status

| Page | Design System | Backend Data | Functional | Rating |
|------|--------------|-------------|-----------|--------|
| Dashboard | Yes | No (static) | Partial | 30% |
| Providers | **No** | **No** | **None** | 0% |
| Models | **No** | **No** | **None** | 0% |
| Policies | **No** | **No** | **None** | 0% |
| Tenants | **Missing page** | — | — | 0% |
| Projects | **Missing page** | — | — | 0% |
| API Keys | **Missing page** | — | — | 0% |
| Requests | **No** | **No** | **None** | 0% |
| Usage | **No** | **No** | **None** | 0% |
| Audit | **No** | **No** | **None** | 0% |

### Backend Endpoints Available

| Endpoint | Method | Data |
|----------|--------|------|
| `/admin/providers` | GET | Real provider registry |
| `/admin/models` | GET | Real model capabilities |
| `/admin/policies` | GET | Built-in profiles |
| `/admin/tenants` | GET/POST | DB-backed CRUD |
| `/admin/tenants/{id}` | GET | Detail |
| `/admin/projects` | GET/POST | DB-backed, filter by tenant |
| `/admin/projects/{id}` | GET | Detail |
| `/admin/api-keys` | GET/POST/DELETE | Full CRUD |
| `/admin/api-keys/{id}` | GET | Detail |
| `/admin/requests` | GET | Real request logs |
| `/admin/requests/{id}` | GET | Detail with tokens/cost |
| `/admin/usage/summary` | GET | Per-project aggregation |
| `/health` | GET | Platform health |

### Missing Frontend Infrastructure
- No `tenants/page.tsx`, `projects/page.tsx`, `api-keys/page.tsx`
- No data fetching hooks
- No POST/DELETE in API client
- No form components
- No modal/drawer components
- No pagination component
- No filter bar component
- No error boundary

## Gap Analysis

| Gap | Impact |
|-----|--------|
| **GAP-F1** All feature pages are dead stubs | Critical |
| **GAP-F2** No data fetching from backend | Critical |
| **GAP-F3** Missing 3 tenant/project/key pages | Critical |
| **GAP-F4** API client only supports GET | High |
| **GAP-F5** No forms, modals, drawers | High |
| **GAP-F6** Dashboard uses static data | Medium |
| **GAP-F7** Sidebar icons are Unicode placeholders | Low |
| **GAP-F8** No responsive behavior | Low |

## Execution Plan

| Task | What | Gaps |
|------|------|------|
| **FE-02** | Theme upgrade + API client expansion | GAP-F4 |
| **FE-03** | App shell + sidebar premium redesign | GAP-F7 |
| **FE-04** | Dashboard with real backend data | GAP-F6 |
| **FE-05** | Providers + Models + Policies (real data) | GAP-F1 |
| **FE-06** | Tenants + Projects + API Keys (full CRUD) | GAP-F1, F3, F5 |
| **FE-07** | Requests + Usage + Audit (real data) | GAP-F1, F2 |
| **FE-08** | UX polish pass | All |
| **FE-09** | Build verification + regression | — |
| **FE-10** | Final polish + docs | — |
