"""FIX35 Part C — the four-step Auto router (OpenRouter's shape, our constraints).

    1. classify   — ~30 fine-grained task types from ONE asset
                    (task_taxonomy.json; taxonomy.py)
    2. rank       — the tenant's own 7-day request_logs: volume, success
                    rate, latency, cost per task type; static default rank
                    until enough samples (ranking.py)
    3. tier       — price band per 1M tokens → low/medium/high/xhigh/max;
                    the request / conversation / tenant / deployment ceiling
                    caps the pool (tiers.py)
    4. order      — an ORDERED list (primary + fallbacks), always filtered to
                    providers that hold a usable key right now (router.py)

Contract (orchestrator._apply_router_chain):
    model="auto"              → this router
    model="<concrete id>"     → never touched
    model="openrouter/auto"   → passed through to OpenRouter's router
    classifier / ranking down → default set, silently (logged); never an error
    no provider with a key    → ProviderUnavailableError (nothing to route)
"""
