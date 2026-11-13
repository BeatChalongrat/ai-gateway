# Routing strategy

Reviewed against source on 2026-11-12. See [routing architecture](ROUTING_ARCHITECTURE.md)
and backend routing/provider modules for the implementation.

Explicit model requests and automatic routing have different selection paths.
Automatic routing combines configured rules, available capability metadata,
policy constraints and scoring. Keyword/semantic/cost-tier paths and fallback
are implemented; they must not all be labelled future work.

Provider/model availability comes from runtime configuration and credentials.
Model discovery/catalog metadata is separate from observed model behavior.
Fallback eligibility, retry limits, recipient restrictions and in-house-only
decisions are enforced during dispatch, not just by the initial route.

For Office work, select a model with actual tool support and enough context.
Source tables of old model IDs are examples, not the current catalog. Follow
the live-test model rule in the repository's CLAUDE.md; a reasoning budget
exhaustion is not automatically a tool transport defect.
