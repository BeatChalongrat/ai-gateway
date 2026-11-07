"""Model discovery package.

- ``pricing_fetcher``  — pulls per-token pricing from the litellm
  community JSON, with a 1h in-process cache and fail-open semantics.
- ``provider_discovery`` — per-provider adapters that return a
  ``list[DiscoveredModel]`` from the provider's ``/v1/models`` API
  (or a static known list when no such API exists).
- ``discovery_service`` — orchestrator that calls the fetcher + every
  adapter, upserts into ``model_configs``, soft-deactivates models
  that disappeared, and triggers a registry reload.
"""
