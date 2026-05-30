# Reasoning Support Audit — What Already Exists (T0)

**Generated:** 2026-05-31
**Scope:** AUDIT ONLY — no code changed. Maps current reasoning-model support (o1/o3, Claude extended thinking, DeepSeek R1, Qwen QwQ, etc.) across the 3-layer stack before any new implementation.
**Method:** full static trace of all three layers (12 gateway adapters + Go chat-edge + Next.js frontend) with file:line evidence, live DB inspection, and live E2E requests against the running stack (gateway :8080, chat-edge :4000 — both UP at audit time).
**Stack:** Next.js (apps/web-chat, :3002) → Go Chat Edge (:4000) → AI Gateway (backend/app, :8080).

> **Relationship to `THINKING_AUDIT_REPORT.md` (2026-05-29):** that report concluded "fully wired, defeated at runtime by router + missing keys." This audit **confirms the wiring**, but goes deeper on the **12 individual adapters** (the prior report collapsed 7 of them into "OpenAI-compatible"), re-runs E2E against a **changed environment** (OpenAI o-series reasoning rows are now seeded + the OpenAI key is valid → reasoning token-accounting now demonstrably fires), and surfaces **3 findings the prior audit missed**: (1) the reasoning UI control path is dead code, (2) Anthropic thinking-signature round-trip for tool loops is impossible through the current schema, (3) a response-cache reasoning-replay contamination path.

---

## 1. Capability Matrix

| Capability | Status | Evidence (file:line) | Notes |
|---|---|---|---|
| **OpenAI reasoning (o1/o3/o4)** | ⚠️ Partial | `openai_adapter.py:103-114` (req map), `:231-239` (parse), `:100-102` (Responses-API comment) | `reasoning_effort` + `max_tokens`→`max_completion_tokens` + strip temp/top_p all correct. **Token count only — no reasoning text** (Chat Completions API). E2E: served, `reasoning_tokens:128`, `reasoning_content` absent. |
| **Anthropic extended thinking** | ✅ (code) / ❌ (live) | `anthropic_adapter.py:110-132` (thinking block + budget), `:461-484` (thinking_delta parse), `parsers.py:95-100` | Most complete adapter: own request build + stream state machine + non-stream concat. **No valid Anthropic key live** (16-char placeholder) → not exercised E2E. |
| **DeepSeek / Groq reasoning (R1)** | ✅ (code) / ❌ (live) | `openai_compatible_adapter.py:97-104`, `parsers.py:47-50`, seed rows `045:103-169` | Native `reasoning_content` both stream + non-stream via shared parser. Seeded rows exist; **no DeepSeek/Groq provider key live**. |
| **Gemini thinking** | ✅ (code) / ❌ (live) | `google_adapter.py:222-234` (thinkingConfig), `:147-154` (thought:true parse), `:114` (thoughtsTokenCount) | Full own parser. **0 Gemini reasoning rows seeded + no Google key** → unreachable live. |
| **OpenRouter reasoning** | ✅ (code) / ❌ (live) | `openrouter_adapter.py:66-75` (include_reasoning + reasoning.effort) | Reuses OpenAI-compat parser. Key is valid live, but **0 OpenRouter reasoning rows seeded**. |
| **Qwen / Kimi / Minimax / Z.AI / xAI reasoning** | ⚠️ Inherit-only | `qwen_adapter.py:13-16`, `kimi_adapter.py:13-16`, `minimax_adapter.py:14-17`, `zai_adapter.py:13-16`, `xai_adapter.py:21,33-54` | Subclass `OpenAICompatibleAdapter` with **zero own reasoning logic** — only base `reasoning_effort` + `delta.reasoning_content`. **No reasoning-flagged model rows seeded** for any → dormant. |
| **OpenAI Responses API** | ❌ Missing | repo-wide grep `/responses` → 0 hits | Every OpenAI/compatible adapter posts `/chat/completions`. So OpenAI o-series can never surface reasoning *summary text* — only counts. |
| **Capability registry (ORM)** | ✅ | `model_config.py:58-66`; migration `045_add_reasoning_capability.py` | `supports_reasoning`, `reasoning_strategy` (String, not enum), `default_reasoning_mode`. **No** `reasoning_effort`/`thinking_budget` columns (effort/budget are adapter constants). |
| **Unified reasoning request schema** | ✅ | `public.py:21-42` (ReasoningConfig), `:132` (request), `:196-197` (response) | modes `off/auto/low/medium/high`, `show_summary`. Surfaced on `GET /v1/models` via `public_models.py:43-60`. |
| **Effort → provider mapping** | ✅ | OpenAI `openai_adapter.py:28-33`; Anthropic `anthropic_adapter.py:49-54`; Gemini `google_adapter.py:50-55` | 3 separate hardcoded maps; gated on `mode!=off` AND `supports_reasoning`. |
| **Normalized SSE reasoning events** | ✅ | `public_chat.py:927-951` (`reasoning_summary_delta`), `:953-973` (`analysis_status`) | Provider-neutral frames — adapters normalize native deltas into `chunk.reasoning_delta`; route consumes only that (`:1011`). |
| **Reasoning token accounting** | ✅ | `orchestrator.py:1012-1023` (non-stream), `public_chat.py:1461-1465` (stream); cols `request_log.py:30-34` | Counted separately from completion; Anthropic estimated `len//4`. Plumbed end-to-end into log-worker proto (`logworker_pb2.py:17`). E2E: `reasoning_tokens:128` returned. |
| **Reasoning cost** | ⚠️ | `usage_service.py:68-93` | Billed at **output rate** (not a distinct reasoning rate). **Additive/double-count risk**: docstring `:76-79` warns it is already inside `estimated_cost_usd` via completion_tokens. |
| **DLP scan of reasoning text** | ✅ | `public_chat.py:1029-1059` (rolling-window), `:1594-1595` (flush) | Reasoning deltas DLP-masked before emit, same as content. Reasoning **text never persisted** — only token count (`:766-769, 1551-1556`). |
| **Go chat-edge relay** | ✅ | `gateway_client.go:433-450` (parse), `handler/message.go:418-426` (re-emit), `conversation_repo.go:380-403` (persist settings) | Full relay: parse → browser SSE → persist → return-on-fetch. |
| **Reasoning persistence + reload** | ✅ | migration `019`, `message_repo.go:107-111,137` (SELECT incl. reasoning_content), `handler/message.go:79,145-147` | `reasoning_content` **returned on `ListByConversation`** → survives page reload (prior audit said it did not; that gap is closed). |
| **ThinkingBlock UI (display)** | ✅ | `ThinkingBlock.tsx:131-138` (text), `:75-78` (duration), `:54-62` (auto-expand/collapse) | Renders actual reasoning text, collapsible, auto-expand-while-streaming / auto-collapse 1s after, duration, bouncing-dot "Analyzing" status. |
| **Reasoning toggle / effort control (UI)** | ❌ Dead code | `ReasoningToggle.tsx` (full impl) **imported nowhere**; `ChatInput.tsx:161-167` removal comment | Control path deliberately removed → "Auto Thinking Mode". User cannot turn reasoning on/off or pick effort. |
| **Model picker reasoning badge** | ❌ Missing | `ModelSelector.tsx:463-500` | `ModelEntry.reasoning` + `ModelCategory "reasoning"` exist in types but never read in picker. |
| **Tool-loop thinking round-trip (Anthropic)** | ❌ Missing | `schemas/common.py:5-32` (no thinking field), `anthropic_adapter.py:477-484` (signature dropped) | Extended-thinking + tool-use across turns is **structurally impossible** — request `Message` has no thinking/signature slot; signature explicitly discarded. |
| **Context-budget mgmt for reasoning** | ❌ Missing | `anthropic_adapter.py:131-132` (inflates max_tokens, no window check) | No truncation/reservation; `max_context_tokens` is metadata only, never consulted. |
| **Reasoning → memory/cache contamination** | ⚠️ Cache leak | `orchestrator.py:1794` (caches reasoning_content), `cache_service.py:68` (key omits reasoning) | History is clean (reasoning not on `Message`). But response cache stores `reasoning_content` and key omits `reasoning` config → can replay user A's reasoning to user B in same project; bypasses per-user restore. |

Legend: ✅ works end-to-end (code) · ⚠️ partial / limited · ❌ missing or dead.

---

## 2. Per-T-Session Status

### T0 — Gateway Provider Adapters · ✅ **~85% complete**
12 adapters audited. Reasoning request-translation + delta-parsing is **fully built** for the 5 first-class strategies (Anthropic, OpenAI o-series, OpenAI-compatible/DeepSeek/Groq, Gemini, OpenRouter). The 5 secondary providers (Qwen/Kimi/Minimax/Z.AI/xAI) inherit base behavior but have **no own reasoning logic and no seeded reasoning models** → dormant. Two real gaps: **Responses API not integrated** (OpenAI text-less), **no tool-loop reasoning handling**. Status per adapter:

| Adapter | Support | Request map | Stream parse | Responses API |
|---|---|---|---|---|
| anthropic | **Full** | `:110-132` | `:461-484` (thinking_delta) | n/a (Messages API) |
| openai | **Partial** (count only) | `:103-114` | `:231-239` (reasoning_content) | ❌ chat/completions only |
| openai_compatible (base) | **Partial** | `:97-104` | `:172` | ❌ |
| deepseek | **Full** (inherited) | base | base reasoning_content | ❌ |
| groq | **Full** (inherited) | base | base reasoning_content | ❌ |
| google | **Full** | `:222-234` | `:147-154` (thought:true) | n/a (generateContent) |
| openrouter | **Full** | `:66-75` | shared parser | ❌ |
| qwen | Inherit-only | base | base | ❌ |
| kimi | Inherit-only | base | base | ❌ |
| minimax | Inherit-only | base | base | ❌ |
| zai | Inherit-only | base | base | ❌ |
| xai | Inherit-only | base | base | ❌ |

### T1 — Capability Registry + Reasoning Request Config · ✅ **~90% complete**
ORM columns, schema, effort→provider mapping, and `GET /v1/models` discovery surface all present and working. Reasoning rows now seeded for Anthropic ×3, DeepSeek, Groq, **and OpenAI o-series ×10** (discovery `source=auto`). Gaps: no `reasoning_effort`/`thinking_budget` DB columns (constants instead); `reasoning_strategy` is a free String not an enum; Gemini/OpenRouter rows still unseeded.

### T2 — Normalized Stream Events + Token Accounting · ✅ **~85% complete**
Normalized `reasoning_summary_delta` + `analysis_status` SSE frames; reasoning tokens counted separately (stream + non-stream) and plumbed through the gRPC log-worker proto (`reasoning_tokens`/`reasoning_cost` columns populated, **not 0**). DLP rolling-window scans reasoning text before emit. Gaps: cost billed at output rate with an **additive double-count risk** (`usage_service.py:76-79`); Anthropic thinking tokens only estimated (`len//4`).

### T3 — Go Chat Edge Relay + Persistence · ✅ **~90% complete**
Full relay confirmed: forwards reasoning config, parses gateway reasoning SSE, re-emits browser SSE on all 3 send paths (send/regenerate/edit), persists `reasoning_content` on completion (migration 019), and **returns it on history fetch → survives reload**. One functional gap: the persisted per-conversation reasoning settings are **vestigial** — `attachReasoning` hardcodes `Mode:"auto", ShowSummary:true` (`message_service.go:2083-2088`), so stored `reasoning_mode`/`show_reasoning_summary` never influence the gateway request.

### T4 — Frontend Reasoning UI · ⚠️ **~55% complete**
Display path **fully built** (ThinkingBlock real-text render + collapsible + auto-expand/collapse + duration + status animation; `consumeStream` parses `reasoning_delta`/`analysis_status`, accumulates, tracks duration; rendered in MessageBubble). Control path **dead**: `ReasoningToggle.tsx` fully implemented but imported nowhere; `setConversationReasoning` store action + PATCH unreachable; model picker shows no reasoning badge. Latent: MessageBubble `areEqual` memo omits the 3 reasoning fields (`:621-648`).

### T5 — Tool Calling + Context + Memory · ⚠️ **~40% complete**
Tool/function calling fully handled in both OpenAI + Anthropic adapters, but **no server-side tool loop** (client-driven; gateway stateless per leg — by design). Three real gaps: **(2) Anthropic thinking-block/signature round-trip is impossible** (schema has no slot, signature dropped) → extended thinking + tool use breaks across turns; **(3) zero context-budget management** for reasoning consumption (adapter even inflates max_tokens with no window check); **(4) response-cache reasoning-replay** contamination across users in a project. History/RAG itself is clean (reasoning not on `Message`). Multi-provider per-request reasoning works statelessly.

---

## 3. What's Already Built (works end-to-end in code)

1. **Unified reasoning contract** — request `ReasoningConfig`, response `reasoning_content`/`reasoning_tokens`, discovery `ReasoningCapability` (`public.py:21-42,132,196-197`; `public_models.py:43-60`).
2. **Five first-class provider strategies** — Anthropic thinking, OpenAI o-series effort, OpenAI-compatible (DeepSeek/Groq) native `reasoning_content`, Gemini thinkingConfig, OpenRouter — each with request-translation + stream + non-stream parsing.
3. **Normalized SSE events** — provider-neutral `reasoning_summary_delta` + `analysis_status` (`public_chat.py:927-973`).
4. **Reasoning token accounting + cost + log-worker plumbing** — separate counting, request_logs columns, gRPC proto field (verified populated; E2E `reasoning_tokens:128`).
5. **DLP masking of reasoning text** before emit; reasoning bytes never persisted.
6. **Full Go relay + persistence + reload** — including `reasoning_content` returned on history fetch (closes the prior audit's P2 reload gap).
7. **ThinkingBlock display** — real reasoning text, collapsible, auto-expand/collapse, duration, "Analyzing" animation, EN+TH i18n.
8. **Frontend stream parsing** — `reasoning_delta`/`analysis_status` accumulation + duration tracking in `consumeStream`.
9. **Auto-thinking enablement** — `orchestrator._maybe_enable_adaptive_reasoning` (`:1923-1958`) turns on `mode=auto` when routing lands on a reasoning-capable model.

## 4. What's Stub / Partial (code exists, not fully working e2e)

1. **OpenAI o-series = token count only** — Responses API not integrated; no reasoning text/summary ever surfaces. (E2E confirmed: content + `reasoning_tokens` but no `reasoning_content`, no stream reasoning events.)
2. **Qwen/Kimi/Minimax/Z.AI/xAI** — mechanically capable via base class but no own logic and **no seeded reasoning rows** → never fire.
3. **Reasoning UI controls** — `ReasoningToggle.tsx` + `setConversationReasoning` fully built but **orphaned** (no caller). Users cannot choose reasoning/effort.
4. **Per-conversation reasoning settings (Go)** — written + read by API but **ignored** when building the gateway request (`attachReasoning` hardcodes auto/show_summary).
5. **Reasoning cost** — computed but additive-double-counts unless a rollup subtracts it (`usage_service.py:76-79`); Anthropic thinking tokens only estimated.
6. **MessageBubble memo** — omits reasoning fields from `areEqual` → latent stale-render risk.

## 5. What's Missing (no implementation)

1. **OpenAI Responses API** — entirely absent (`/responses` 0 hits). Blocks visible OpenAI reasoning summaries.
2. **Reasoning-aware routing** — `model=auto` + reasoning silently downgrades to a non-reasoning model. **E2E confirmed: `auto` → `gpt-4o-mini`, reasoning dropped, no error.** (Unchanged from prior audit — still the top P0.)
3. **Anthropic thinking-block/signature round-trip** for tool loops — no schema slot; signature dropped (`anthropic_adapter.py:477-484`). Extended thinking + tool use cannot work across turns.
4. **Context-budget management** accounting for reasoning token consumption — none.
5. **Response-cache reasoning isolation** — cache key omits `reasoning`; stored `reasoning_content` can replay to other users and bypass per-user DLP restore.
6. **Model-picker reasoning badge** / reasoning-model grouping in UI.
7. **Gemini + OpenRouter reasoning model rows** — adapters ready, 0 rows seeded.
8. **Reasoning-aware downgrade signal** — when a reasoning request resolves to a non-reasoning model, nothing tells the client.

---

## 6. End-to-End Test Results (live, gateway :8080, 2026-05-31)

Auth: `GATEWAY_API_KEY` (gw_4be1…). Environment changed since prior audit: OpenAI o-series reasoning rows now seeded (`source=auto`) and OpenAI key is **valid** (220-char).

| Test | Requested | Served | reasoning_tokens | reasoning_content / events | Result |
|---|---|---|---|---|---|
| o4-mini non-stream, `mode=medium`, show_summary | `o4-mini` | **`o4-mini`** (openai) | **128** | `reasoning_content` absent | ⚠️ Token accounting works; **no visible reasoning** (Chat Completions API) |
| o4-mini stream, `mode=medium`, show_summary | `o4-mini` | `o4-mini` | — | only `chat.completion.chunk` (2); **no `analysis_status`/`reasoning_summary_delta`** | ⚠️ OpenAI emits no reasoning deltas |
| `auto` + `mode=high`, show_summary | `auto` | **`gpt-4o-mini`** (openai) | None | absent | ❌ **Router silently downgraded → reasoning discarded** |
| Anthropic / DeepSeek / Gemini visible text | — | — | — | — | ⏭️ Untestable: no valid keys (Anthropic placeholder; DeepSeek/Groq/Google absent) |

**Key live findings:**
- **Explicit reasoning model is now honored** (o4-mini was NOT overridden) and **reasoning token accounting fires end-to-end** — a concrete improvement over the prior audit's environment.
- **`auto` + reasoning still silently downgrades** to `gpt-4o-mini` (non-reasoning) with no error/signal — the #1 P0 is unchanged.
- **OpenAI path is structurally text-less** (count only, no deltas) — visible thinking requires Anthropic/DeepSeek/Gemini keys, which are absent here.

Live DB at audit time:
```
reasoning models (supports_reasoning=true): anthropic ×3, deepseek ×1, groq ×1, openai ×10  (15 rows)
provider_keys: openai valid(220) + invalid(8); openrouter valid(100)+valid(12,inactive)+untested(12); anthropic untested(16)
```

---

## 7. Revised T0–T5 Plan (effort adjusted to what already exists)

| Session | Already done | Remaining work | Revised effort |
|---|---|---|---|
| **T0 — Adapters** | 5 first-class strategies fully built + parsed | (a) Integrate OpenAI **Responses API** for visible o-series reasoning; (b) optionally add own reasoning rows/logic for Qwen-QwQ etc. — only seeding needed, not adapter code | **Light–Medium** (mostly Responses API; rest is config/seed) |
| **T1 — Registry/config** | ORM + schema + mapping + discovery + most seeds | Seed Gemini/OpenRouter reasoning rows; optional `reasoning_effort`/`thinking_budget` columns; make `reasoning_strategy` an enum | **Light** (mostly data/migrations) |
| **T2 — Stream/accounting** | Normalized events + token accounting + DLP + log-worker proto | Fix reasoning-cost **double-count** (`usage_service.py:76-79`); separately estimate Anthropic thinking tokens | **Light** (billing correctness) |
| **T3 — Go relay** | Full relay + persistence + reload | Decide intent on **vestigial per-conversation settings** — either honor stored prefs in `attachReasoning` or remove the columns | **Light** (1 wiring decision) |
| **T4 — Frontend UI** | Full display path (ThinkingBlock + parsing + render) | **Re-wire `ReasoningToggle`** into ChatInput (or formally drop it); add model-picker reasoning badge; add the 3 reasoning fields to MessageBubble `areEqual` | **Medium** (re-enable dead control path + UX) |
| **T5 — Tool/context/memory** | Tool calling in both adapters; clean history; stateless multi-provider | **Anthropic thinking-block/signature round-trip** (schema + adapter — the biggest item); **context-budget mgmt** for reasoning; **fix cache reasoning-replay** (add `reasoning` to cache key + restore reasoning_content per-user) | **Heavy** (schema change + new logic) |
| **Cross-cutting P0 (routing)** | — | **Reasoning-aware routing**: when `mode!=off`, restrict candidates to `supports_reasoning` or honor the explicit model; emit a downgrade signal when it can't | **Medium** (router change; highest user impact) |

**Bottom line:** The reasoning stack is **substantially built** (T0–T3 ~85–90%, display ~55%, T5 ~40%) — far more than a stub. The highest-leverage remaining work is **not** adapter plumbing but: (1) reasoning-aware **routing** (the live blocker), (2) OpenAI **Responses API** for visible o-series reasoning, (3) re-enabling the **frontend control path**, and (4) the **tool-loop thinking round-trip + cache isolation** in T5. None of T0–T3 needs to be rebuilt; most remaining items are config/seed, billing correctness, one Go wiring decision, and re-connecting already-built UI.
