# P6-01: Observability Inventory & Gap Analysis

## Current Observability Surface

### What Exists

| Component | Status | Quality |
|-----------|--------|---------|
| DLP detection events (`dlp_finding`) | Logged per match | Good — privacy-safe, structured |
| DLP block warnings (`request_blocked`) | Logged with context | Good — includes tenant/project/request_id |
| Provider fallback warnings | Logged on failure | Good — structured, includes remaining count |
| Error telemetry | Logged with request_id | OK — missing tenant/project context |
| Request ID propagation | Middleware → headers → error bodies | Good |
| `RequestLogORM` table | Schema defined, **never written to** | Unused |
| `UsageService.estimate()` | Heuristic token estimation | Not logged, not persisted |
| `RoutingResult.to_trace_dict()` | Structured trace available | Not logged, not persisted |

### What's Missing

| Gap | Area | Severity |
|-----|------|----------|
| **GAP-O1** | No request lifecycle events (start/end/latency) | High |
| **GAP-O2** | No observability domain model (events are ad hoc) | High |
| **GAP-O3** | `record_success()` doesn't actually log or persist | High |
| **GAP-O4** | No usage/cost records persisted | High |
| **GAP-O5** | `RequestLogORM` exists but is never INSERT-ed | High |
| **GAP-O6** | No provider execution timing | Medium |
| **GAP-O7** | Error handler lacks tenant/project context | Medium |
| **GAP-O8** | No transformation audit (mask/redact operations logged) | Medium |
| **GAP-O9** | No query layer for audit/usage retrieval | Medium |
| **GAP-O10** | Admin `/admin/requests` endpoint returns hardcoded data | Low |

### Privacy-Sensitive Logging Risks

| Risk | Current Status |
|------|---------------|
| Raw prompts in logs | **Safe** — not logged anywhere |
| Raw secrets in logs | **Safe** — DLPAuditEntry strips `matched_text` |
| Provider responses in logs | **Safe** — not logged |
| API keys in logs | **Safe** — only hash/prefix stored |
| Tenant/project IDs in logs | OK — these are non-sensitive identifiers |

### Available Data Sources (Not Yet Connected)

| Source | Data Available | Used For Observability |
|--------|---------------|----------------------|
| `RequestContext` | tenant_id, project_id, request_id | Partially (DLP block only) |
| `RoutingResult` | selected/rejected/fallbacks/reasons | **Not logged** |
| `ProviderResponse` | content length, model, finish_reason, usage | **Not logged** |
| `EnforcementDecision` | match_actions, overall_action, reasons | Partially (DLP events) |
| `time.perf_counter()` | Latency measurement | **Not captured** |

## Execution Plan

| Task | Gaps Addressed | Priority |
|------|---------------|----------|
| **P6-02** Domain model + event contracts | GAP-O1, GAP-O2 |
| **P6-03** Structured lifecycle logging | GAP-O1, GAP-O3, GAP-O6, GAP-O7 |
| **P6-04** Usage/cost accounting model | GAP-O4 |
| **P6-05** Provider/routing/policy audit integration | GAP-O8, routing trace |
| **P6-06** Outcome/error telemetry | GAP-O7 |
| **P6-07** Persistence (request log + usage) | GAP-O5, GAP-O10 |
| **P6-08** Query/service layer | GAP-O9 |
| **P6-09** Integration tests | All |
| **P6-10** Docs | All |
