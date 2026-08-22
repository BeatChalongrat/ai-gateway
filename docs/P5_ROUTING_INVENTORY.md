# P5-01: Routing Inventory & Decision-Point Gap Analysis

## Current Routing Decision Points

### 1. RoutingService.select_route() — PRIMARY
**File:** `app/services/routing_service.py`

The main routing entry point, called by the orchestrator. Current logic:

| Condition | Provider | Model | Reason |
|-----------|----------|-------|--------|
| `model != "auto"` | `resolve_provider(model)` or fallback `"openai"` | as-requested | Explicit model |
| `model == "auto"` + `use_case in {coding, reasoning}` | `anthropic` | `claude-3-5-sonnet-20241022` | Use-case routing |
| `model == "auto"` (default) | `openai` | `gpt-4o-mini` | Cost-efficient default |

**Issues:**
- No capability filtering (e.g. streaming support, context length)
- No project/policy constraints consumed (policy_decision param is accepted but unused)
- No fallback candidates — returns exactly one route
- No cost/latency awareness
- Unknown models silently fall back to OpenAI with no explanation

### 2. Adapter-Level "auto" Resolution — SCATTERED
**Files:** `openai_adapter.py`, `anthropic_adapter.py`, `openrouter_adapter.py`

Each adapter independently resolves `"auto"` to a default model:
- OpenAI: `"auto"` → `"gpt-4o-mini"`
- Anthropic: `"auto"` → `"claude-3-5-sonnet-20241022"`
- OpenRouter: `"auto"` → `"openai/gpt-4o-mini"`

**Issue:** This duplicates the routing service's responsibility. If the routing service resolves `"auto"` to a specific model, the adapter should never see `"auto"`. But if the routing service passes `"auto"` through, the adapter makes a hidden routing decision.

**Fix (P5-03):** Ensure routing always resolves to a concrete model before dispatch. Remove "auto" fallback from adapters.

### 3. Model Registry — LOOKUP ONLY
**File:** `app/services/providers/model_registry.py`

Maps model names to providers via exact match or prefix. Currently has no capability metadata — only `(model, provider)` pairs.

**Issue:** No filtering by capabilities (streaming, tools, context length). The `ProviderModel` dataclass in `entities.py` has `supports_tools`, `supports_streaming`, `risk_level`, `region` fields, but the registry doesn't use them.

### 4. Orchestrator Streaming Path
**File:** `app/services/orchestrator.py` (resolve_streaming_context)

Checks `adapter.supports_streaming` before allowing streaming. This is a routing-adjacent decision made outside the routing service.

## Available Routing Inputs

| Input | Source | Currently Used | Available |
|-------|--------|---------------|-----------|
| `payload.model` | Public request | Yes | Yes |
| `payload.metadata.use_case` | Public request | Yes | Yes |
| `payload.stream` | Public request | No (in routing) | Yes |
| `ctx.tenant_id` | Phase 4 auth | No | Yes |
| `ctx.project_id` | Phase 4 auth | No | Yes |
| `ctx.policy_profile_id` | Phase 4 project | No | Yes |
| `policy_decision` | DLP/policy eval | **Ignored** | Yes |
| Model capabilities | Model registry | No | Via `ProviderModel` |
| Provider availability | Runtime | No | Future |
| Cost metadata | Model registry | No | Via `ProviderModel` extension |

## Gap Analysis

### GAP-R1: Routing Contracts Are Minimal
**Current:** `RouteDecision` has `provider_name`, `model_name`, `reasons`, `constrained_by_policy`. No structured input model, no candidate set, no rejection tracking.
**Fix (P5-02):** Introduce `RoutingRequest`, `RoutingCandidate`, enriched `RouteDecision`.

### GAP-R2: "auto" Resolved in Two Places
**Current:** RoutingService resolves "auto" to a model+provider. Adapters also resolve "auto" independently.
**Fix (P5-03):** Guarantee routing always resolves to a concrete model. Remove "auto" handling from adapters.

### GAP-R3: No Capability Filtering
**Current:** No check for streaming support, tool support, or context length before routing.
**Fix (P5-04):** Enrich model registry with capabilities. Filter candidates before selection.

### GAP-R4: Policy/Project Constraints Ignored
**Current:** `policy_decision` parameter in `select_route` is accepted but never read. No project-level provider restrictions.
**Fix (P5-05):** Read policy constraints (e.g. allowed_providers, blocked_models).

### GAP-R5: No Fallback Strategy
**Current:** Routing returns exactly one candidate. If that provider fails, the request fails.
**Fix (P5-06):** Return ranked candidates. Dispatcher tries next candidate on failure.

### GAP-R6: No Cost/Latency Awareness
**Current:** Auto-routing always picks `gpt-4o-mini` (cheapest OpenAI) or `claude-3-5-sonnet` (for coding). No scoring.
**Fix (P5-07):** Add simple cost/latency tiers to model metadata. Score candidates.

### GAP-R7: Route Decisions Not Explainable
**Current:** `RouteDecision.reasons` is a `list[str]` — unstructured. No tracking of rejected candidates or scoring.
**Fix (P5-08):** Structured `RouteTrace` with candidates considered, scores, rejections.

## Execution Plan

| Order | Task | Gaps Addressed |
|-------|------|---------------|
| 1 | **P5-02** Routing domain model | GAP-R1 |
| 2 | **P5-03** Rule-based routing baseline | GAP-R2 |
| 3 | **P5-04** Capability filtering | GAP-R3 |
| 4 | **P5-05** Policy-aware routing | GAP-R4 |
| 5 | **P5-06** Fallback strategy | GAP-R5 |
| 6 | **P5-07** Cost/latency awareness | GAP-R6 |
| 7 | **P5-08** Route trace model | GAP-R7 |
| 8 | **P5-09** Integration tests | All |
| 9 | **P5-10** Docs | All |

**Safest first task: P5-02** — contracts are additive and unblock all subsequent routing work.
