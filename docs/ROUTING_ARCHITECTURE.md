# Routing Engine Architecture

The routing engine decides which provider and model handles each request. It operates between the public API and provider adapters, consuming project/policy context and producing ranked candidates.

## Pipeline

```
Request
  |
  v
[Build RoutingRequest]  Extract model, use_case, stream, constraints
  |
  v
[Resolve]  Explicit model → model registry lookup
  |        Auto model → use_case-based selection
  v
[Filter]   Capability filtering (streaming, provider, blocked models)
  |
  v
[Score]    Cost/latency scoring for candidate ordering
  |
  v
[Select]   Primary candidate + ranked fallbacks
  |
  v
[Dispatch]  Try primary → fallback on provider failure
```

## Routing Modes

### Explicit Model
When `model != "auto"`, the routing engine resolves the provider via the model registry:
- Exact match: `gpt-4o` → `openai`
- Prefix match: `claude-*` → `anthropic`, `meta-llama/*` → `openrouter`
- Unknown model: falls back to `openai` with a warning reason

### Auto Routing
When `model == "auto"`, selection depends on `metadata.use_case`:

| use_case | Provider | Model | Why |
|----------|----------|-------|-----|
| `general` (default) | openai | gpt-4o-mini | Cost-efficient default |
| `coding` | anthropic | claude-3-5-sonnet-20241022 | Reasoning-optimized |
| `reasoning` | anthropic | claude-3-5-sonnet-20241022 | Reasoning-optimized |

## Capability Filtering

Candidates are filtered before selection:

| Filter | Description |
|--------|-------------|
| `requires_streaming` | Excludes models that don't support streaming |
| `allowed_providers` | Only route to specified providers |
| `blocked_models` | Exclude specific model names |

Pass constraints via `metadata`:
```json
{
  "model": "auto",
  "metadata": {
    "allowed_providers": ["openai", "anthropic"],
    "blocked_models": ["gpt-3.5-turbo"]
  }
}
```

### Naming the recipients (`metadata.allowed_recipients`)

`allowed_providers` names routes. A route is not always the company that reads
the request: behind a router (OpenRouter) a model is run by its maker or by a
hosting company, and unless told otherwise the router picks one per request.
A caller that must reach named recipients only sends a list of recipient keys
— `<route>` for a direct provider, `<route>:<company>` behind a router:

```json
{
  "model": "google/gemini-2.5-flash",
  "metadata": { "allowed_recipients": ["openrouter:google", "deepseek"] }
}
```

With the key present (`app/services/providers/recipients.py`):

- an in-house provider is not a recipient and is always reached;
- any other call whose recipient is not on the list is refused before anything
  is sent — `400 no_allowed_provider`, `details.reason` =
  `recipient_not_allowed`. The check is made at the boundary (before a stream
  begins) and again where the provider's adapter is called, so a second route
  and a rescue by another model are held by the same line;
- `openrouter:google` is not `google`: the company reached directly and the
  company reached through the router are two recipients;
- a model whose company cannot be named — the router's own `auto`, a maker
  that is not in the table — has no key, so it can be on no list
  (`recipient_cannot_be_named`);
- an empty list, `null`, or a value that is not a list means **nobody
  outside**. Only the key being absent means the caller did not ask;
- the request to OpenRouter is pinned: `provider: {only: [the company's own
  endpoints], allow_fallbacks: false, data_collection: "deny"}`. With none of
  them up the request fails there; it is not re-routed to another host.

`GET /v1/models` says each model's `recipient` and `recipient_company`, so a
caller builds its list from the catalogue and types no names. Both are absent
for an in-house model, and `recipient` is absent where the company cannot be
named. `backend/tests/test_recipients.py` holds the refusals.

## Cost/Latency Scoring

Each model has `cost_tier` and `latency_tier` metadata:

| Tier | Cost | Latency |
|------|------|---------|
| budget | Cheapest | Fastest |
| standard | Middle | Middle |
| premium | Most expensive | Slowest |

Pass preferences via `metadata`:
```json
{"model": "auto", "metadata": {"prefer_cost": true}}
```

Scoring influences fallback ordering, not primary selection (primary is use_case-based).

## Fallback Strategy

When the primary provider fails:
1. Dispatcher tries the primary candidate
2. On `ProviderError` (timeout, unavailable, server error), tries next fallback
3. Fallbacks are capped at 2 additional candidates
4. If all fail, the last error is raised

Non-retryable errors (400, 401) do NOT trigger fallback.

## Route Decision Trace

Every routing result includes structured trace data for audit:

```json
{
  "selected": {"provider": "openai", "model": "gpt-4o-mini", "cost_tier": "budget"},
  "fallbacks": [{"provider": "anthropic", "model": "claude-3-haiku-20240307"}],
  "rejected": [{"provider": "openrouter", "model": "x", "reason": "provider not allowed"}],
  "reasons": ["Auto-routing for use_case='general' selected 'gpt-4o-mini' on 'openai'."],
  "constrained_by_policy": false
}
```

## Module Map

```
app/
  domain/
    routing.py              ModelCandidate, RoutingRequest, RoutingResult,
                             RejectedCandidate

  services/
    routing_service.py      RoutingService.route() — main entry point
                             + select_route() legacy bridge

    providers/
      model_registry.py     Model catalog with capabilities, filtering

    provider_dispatcher.py  dispatch() + dispatch_with_fallback()
```

## Model Registry

Models are registered with full capability metadata in `model_registry.py`:

| Model | Provider | Streaming | Cost | Latency |
|-------|----------|-----------|------|---------|
| gpt-4o | openai | Yes | standard | standard |
| gpt-4o-mini | openai | Yes | budget | fast |
| gpt-4-turbo | openai | Yes | premium | slow |
| gpt-3.5-turbo | openai | Yes | budget | fast |
| claude-3-5-sonnet-20241022 | anthropic | No | standard | standard |
| claude-3-opus-20240229 | anthropic | No | premium | slow |
| claude-3-haiku-20240307 | anthropic | No | budget | fast |

## Adding a Routing Rule

1. For new use_case defaults: add to `_AUTO_DEFAULTS` in `routing_service.py`
2. For new models: add `ModelCandidate` to `_CANDIDATES` in `model_registry.py`
3. For new providers: add prefix to `_PREFIX_MAP` and adapter to dispatcher
4. For project-level constraints: pass via `metadata.allowed_providers` / `metadata.blocked_models`

## Known Limitations

- Project-level constraints are passed via `metadata`, not stored in DB yet
- No real-time provider health checks (future: circuit breaker pattern)
- Streaming filtering is binary — no partial streaming support
- Cost/latency tiers are static metadata, not measured
- No A/B testing or traffic splitting
