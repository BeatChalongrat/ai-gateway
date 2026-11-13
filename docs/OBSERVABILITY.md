# Observability, audit and usage

Source review: 2026-11-12.

Request identity and timing flow through the Go gateway and FastAPI backend.
The backend records routing, policy, provider, token usage, cost and failures;
request persistence is wired, including streaming paths. Admin UI and
Prometheus/Grafana provide views. The previous “not yet persisted/no dashboard”
limitations are obsolete.

## What to inspect

| Evidence | Owner/reference |
|---|---|
| Request ID and HTTP status | gateway middleware; backend request context/error handler |
| Model/provider/fallback | orchestrator, provider_dispatcher, routing services |
| Request records and traces | request_log_service, trace services, admin request endpoints |
| DLP findings/actions | detector/policy pipeline and privacy-safe audit events |
| Credits/usage | backend ledger/usage services and model configuration |
| Metrics/health/alerts | [monitoring runbook](ops/monitoring.md), infra/prometheus, infra/grafana |
| Office generation failures | chat-edge plan outcomes/change logs and sandbox process result |
| Desktop transfer/tool failures | host tool results, coordinator activity and host logs |

DLP audit events contain metadata/counts rather than matched values. That does
not describe every data store: encrypted raw traces, masked request content,
conversation text and original uploaded files have distinct purposes and
retention. See [DLP](DLP_ARCHITECTURE.md) and [retention](DATA_RETENTION_PLAN.md).

## Interpretation

A successful HTTP request is not necessarily a successful Python run. Check
exit code/timeout and actual persisted artifacts. A valid OOXML/PDF structure
does not prove correct numbers, preserved macros or acceptable visual layout.

Model prices/catalog entries are runtime configuration. Historical price
examples are not current billing truth; inspect configured model rates and
whether usage was measured or estimated. Actual providers after fallback may
differ from the requested provider.

For a rollout, record image revision, request ID, runtime flags, tool outcome,
file IDs and validation evidence. Avoid placing document contents or credentials
in operational reports. The [Office acceptance checklist](OFFICE_WORKFLOWS.md)
separates structural tests from real-model and native-UI acceptance.
