# Routing engine architecture

Source review: 2026-11-12. Routing chooses candidates; dispatch still enforces
policy, recipient restrictions and fallback eligibility.

## Flow

```text
identity/project + request model/capabilities
  -> explicit selection or configured auto-routing
  -> policy/capability/provider filtering
  -> ranking and primary candidate
  -> provider dispatcher
  -> eligible fallback / final error
```

Core code: `backend/app/services/routing_service.py`,
`services/providers/model_registry.py`, `services/provider_dispatcher.py`
and `services/gateway/fallback_orchestrator.py`.

The dispatcher supports a primary request plus bounded Layer-1 alternatives;
the orchestrator's cross-provider tier chain is a separate Layer-2 path.
Timeouts, rate limits, upstream failures and unavailable models have classified
fallback behavior. Authentication/invalid-request failures are not universally
retryable. Read `is_fallback_eligible` rather than applying “retry every error.”

Runtime provider keys/endpoints and model configuration/discovery determine what
is usable. The static catalog is a fallback, not a current inventory of every
model. Keyword, semantic and cost-tier routing code exists; activation depends
on configuration and model availability. Circuit breakers/concurrency controls
and recipient/in-house restrictions also affect dispatch.

## Office implications

Office tasks need actual tool support, sufficient context and reliable tool
results. A catalog capability flag is not a completed acceptance test.
Preserve the chosen model's identity after fallback and report execution
failures distinctly from empty or budget-exhausted model responses.

See [provider architecture](PROVIDER_ARCHITECTURE.md), [DLP](DLP_ARCHITECTURE.md)
and [Office workflows](OFFICE_WORKFLOWS.md). Live test model restrictions are in
the repository's CLAUDE.md.
