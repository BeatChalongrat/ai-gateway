# Enterprise controls

Source review: 2026-11-12. Capability presence and customer deployment policy
are separate; this is not a compliance certification.

| Area | Current implementation/reference |
|---|---|
| Admin identity | Cookie/session authentication and role checks, not unauthenticated admin endpoints; [Admin API](ADMIN_API.md) |
| User identity | Portal OAuth/session authority; chat-edge verifies Portal JWT; [SSO runbook](SSO-GO-LIVE.md) |
| Tenant/project scope | Request identity, repository scoping and endpoint authorization |
| Usage/cost | Credits, quotas, model/provider configuration and usage records; backend services and admin UI |
| Rate limiting | Redis-backed paths and optional Rust limiter; not daily quotas alone |
| Data policy | Text DLP, explicit raw-egress controls and trusted-caller policy; [DLP](DLP_ARCHITECTURE.md) |
| Audit/monitoring | Admin audit, request records/traces, Prometheus/Grafana and configured alerts |
| Retention/export | Implemented scheduled mechanisms and user export/deletion; [retention](DATA_RETENTION_PLAN.md) |
| Local files | Electron grants/consent and explicit server transfer; [Office](OFFICE_WORKFLOWS.md) |

Role definitions remain in `backend/app/domain/rbac.py`; the actual admin
routers use `require_admin_session` and route-specific checks. An old API key
role table is not sufficient to describe the browser admin session boundary.
Provider keys/endpoints, models and policy CRUD have live management surfaces;
do not use direct database edits as the default operator workflow.

Original files and conversation data have different storage/retention rules
from DLP audit events. Text masking does not redact a scanned page's image.
Enterprise review must cover configured providers, external destinations,
retention switches, backups and desktop state.

Generic enterprise IdP federation, SCIM or regulated compliance must not be
inferred from Google SSO or a generic identity interface. Consult the current
implementation and [open decisions](OPEN_DECISIONS.md) for requirements beyond
the deployed identity topology.
