# Auto Model Routing Audit — Chat-Edge vs Gateway Smart Routing

Date: 2026-05-31 · Mode: **audit only, no code changed** · Stack live during audit (chat-edge :4000, gateway :8080)

---

## 1. Executive Summary

**Smart routing ทำงานจริงหรือถูก bypass?**
❌ **ถูก bypass ทั้งหมด.** Gateway มี smart-routing pipeline 4 ชั้น (Project Keyword Rules → Semantic Router → Keyword Router → Routing Service) wired ครบจริง — แต่ **ไม่เคยทำงาน** เพราะ entry gate `if payload.model != "auto": return "explicit"` (`orchestrator.py:2090-2092`). Gateway จะรัน pipeline เฉพาะเมื่อ `model == "auto"` เท่านั้น.

**Chat-edge hardcode model ไหม?**
✅ **ใช่ — chat-edge resolve "Auto" เป็น concrete model ก่อนส่ง.** `resolveAutoIfNeeded` (`message_service.go:2156`) แปลง `"auto"` → model id จริง (default = **`claude-sonnet-4-20250514`**) ผ่าน heuristic ใน `model_resolver.go` แล้ว lock ไว้ทั้ง conversation (Fix 5). Gateway จึงรับ concrete model เสมอ → ตกเข้า explicit path → bypass.

**Gap ระหว่าง design กับ reality:**
Design ตั้งใจให้ gateway เป็นสมอง routing (4 ชั้น + cost/tier scoring + provider filter). Reality: **chat-edge เป็นผู้ตัดสิน model คนเดียว** ด้วย heuristic rule-based ง่ายๆ (ภาพ/ไฟล์/ความยาว/มี code) และ gateway แค่ dispatch ตามนั้น. Smart-routing code ทั้งกองเป็น dead path. ซ้ำร้าย Semantic Router ก็ **ปิดอยู่** (`semantic_router_enabled=False`) และ model id ใน map หลายที่ **เก่า** (`claude-3-5-sonnet-20241022`).

**Live evidence:** `routing_decision … "router": "explicit", "model": "claude-sonnet-4-20250514"` (gateway log, req_b7bdd564, 2026-05-31T11:27).

---

## 2. Request Flow Trace

### Actual flow (ตอนนี้)
```
User [Auto]
  → chat-edge resolveAutoIfNeeded → ResolveAutoModelWithReasoning
       → concrete "claude-sonnet-4-20250514"  (heuristic + Fix-4 default, locked per conv)
  → gateway  payload.model = "claude-sonnet-4-20250514"  (≠ "auto")
       → orchestrator: router="explicit"  ← BYPASS 4 layers
       → routing_service._route_explicit → provider=anthropic (resolve_provider)
       → key check post-route → anthropic key 401 → reactive fallback → openai/gpt-4o-mini
  → provider [gpt-4o-mini via fallback]
```

### Design flow (ที่ตั้งใจไว้)
```
User [Auto]
  → chat-edge  model = "auto"   (pass-through, no local resolve)
  → gateway [Project Keyword Rules → Semantic Router → Keyword Router → Routing Service]
       → cost/tier scoring + allowed_providers (key-valid) filter
  → provider [best-fit, key-valid]
```

**ความต่างหลัก:** chat-edge resolve เองแล้ว lock → gateway ไม่เคยเห็น `"auto"` → 4 layers ตายสนิท.

---

## 3. Layer-by-Layer Status

| Layer | Component | Status | Evidence (file:line) | Notes |
|---|---|---|---|---|
| Chat-edge auto resolve | `resolveAutoIfNeeded` | ✅ ทำงาน (เป็น router ตัวจริง) | `services/chat-edge-api/internal/service/message_service.go:2156-2213` | แปลง auto→concrete; heuristic ภาพ/ไฟล์/len>500/turn>30/code-marker → flagship, else fast |
| Chat-edge heuristic | `ResolveAutoModelWithReasoning` | ✅ ทำงาน | `model_resolver.go:192-278` | default=`claude-sonnet-4-20250514` (`defaultModelPreference` :162-163); empty-catalog fallback=`gpt-4o-mini` |
| Chat-edge model lock | Fix 5 `LockedModel` | ✅ ทำงาน | `message_service.go:2167-2201` | resolve ครั้งแรกแล้ว lock ทั้ง conv → ส่ง model เดิมทุก turn |
| Chat-edge → gateway payload | `GatewayChatRequest.Model` | ✅ ส่ง concrete | `message_service.go:2358`, `gateway_client.go` | ไม่เคยส่ง `"auto"` |
| **Gateway routing gate** | `_route_via_pipeline` (entry) | ⚠️ **WIRED แต่ bypass** | `orchestrator.py:2090-2092` | `if payload.model != "auto": router="explicit"; return` → ข้ามทุก layer |
| L1 Project Keyword Rules | `get_project_scoped_hint` | ⚠️ wired, ไม่ถึง | `keyword_router.py:152-195`; called `orchestrator.py:2183-2192` | รันเฉพาะ path `auto` (ไม่เคยมาถึง); DB rules อย่างเดียว |
| L2 Semantic Router | `SemanticRouter` | ❌ **ปิด + stale + ไม่ถึง** | inst `orchestrator.py:201-207`; gate `config.py:250` (`=False`); map `semantic_router.py:119-126` | live `semantic_router_enabled=False`; model id เก่า `claude-3-5-sonnet-20241022` |
| L3 Keyword Router | `get_model_hint` + `DEFAULT_RULES` | ⚠️ wired, ไม่ถึง + DB ว่าง | rules `keyword_router.py:41-83`; called `orchestrator.py:2212-2219` | DEFAULT_RULES = gpt-4o/gpt-4o-mini (ไม่มี Anthropic); `keyword_rules` table = **0 rows** |
| L4 Routing Service | `select_route`/`_route_auto` | ⚠️ wired; auto-path ไม่ถึง | `routing_service.py:183-185`; called `orchestrator.py:695` | auto default coding=`claude-3-5-sonnet-20241022` (stale); fallback=`gpt-4o-mini` |
| Explicit route (ที่ใช้จริง) | `_route_explicit` | ✅ ทำงาน (path เดียวที่รัน) | `routing_service.py:187-231` | resolve_provider + enforce region/policy + allowed_providers; **ไม่เช็ค key validity** |
| Provider filter (key-valid) | `allowed_providers` / key check | ⚠️ proactive บาง path, reactive บน explicit | proactive: `semantic_router.py:216`, `keyword_router.py:144-147`, reasoning `orchestrator.py:2056-2062`; reactive: `orchestrator.py:713-721` | explicit path เลือก anthropic(key เสีย)ได้ แล้วค่อย fail→fallback |
| Fallback chain | `model_tiers.py` + dispatch | ✅ ทำงาน (หลัง fix ก่อนหน้า) | `model_tiers.py` `canonical_model()`/`get_fallback_chain`; `fallback_orchestrator.py` | dated id → canonical → openai/gpt-4o-mini; ครบ |

Legend: ✅ ทำงานจริง · ⚠️ wired แต่ bypass/ไม่ถึง · ❌ ปิด/ไม่ทำงาน

---

## 4. Model Map Currency

| ตำแหน่ง | Model ใน code | ปัจจุบันควรเป็น | ต้อง update? |
|---|---|---|---|
| `semantic_router.py:120-122` INTENT_MODEL_MAP (coding/analysis/creative) | `claude-3-5-sonnet-20241022` | `claude-sonnet-4-20250514` | ✅ stale (แต่ moot — router ปิด + path ไม่ถึง) |
| `semantic_router.py:123-125` (simple_qa/translation/general) | `gpt-4o-mini`, `qwen-turbo`, `qwen-plus` | gpt-4o-mini OK; qwen ไม่มี key/adapter | ⚠️ qwen ไม่มี provider |
| `routing_service.py:105-106` AUTO coding default | `claude-3-5-sonnet-20241022` | `claude-sonnet-4-20250514` | ✅ stale |
| `routing_service.py:102-103` AUTO general/fallback | `gpt-4o-mini` | `gpt-4o-mini` | ✅ valid |
| `keyword_router.py:41-83` DEFAULT_RULES | `gpt-4o`, `gpt-4o-mini` | valid | ⚠️ ไม่มี Anthropic option |
| `model_resolver.go:60` reasoning ids | `claude-sonnet-4-20250514`, `claude-opus-4-20250514` | current | ✅ ตรง |
| `model_resolver.go:162-163` defaultModelPreference | `claude-sonnet-4-20250514` | current | ✅ ตรง |
| `config.go:156-172` DefaultModels catalog | gpt-4o, gpt-4o-mini, o3-mini, o4-mini, claude-sonnet-4-20250514 | current | ✅ ตรง |

**สรุป currency:** chat-edge catalog ทันสมัย (claude-sonnet-4). Gateway smart-routing maps **เก่า** (claude-3-5-sonnet-20241022) — แต่เป็น dead path เลย impact = 0 ตอนนี้; จะ bite ทันทีถ้าเปิด gateway routing.

---

## 5. Config Status

| Setting | Value (live) | Impact |
|---|---|---|
| `semantic_router_enabled` | **False** (`config.py:250`; ยืนยันจาก live settings) | Semantic Router ปิด → ถ้า gateway routing ทำงาน จะข้าม L2 ไป keyword/service |
| `semantic_router_model` | `all-MiniLM-L6-v2` | (ไม่ถูกโหลดเพราะปิด) |
| `semantic_router_threshold` | `0.65` | (n/a) |
| `ANTHROPIC_API_KEY` (env) | present `sk-ant-…` แต่ **invalid (401)** | auto default = Claude → 401 → reactive fallback |
| `OPENAI_API_KEY` (env) | present, **valid** | fallback target ใช้งานได้ |
| `OPENROUTER_API_KEY` (env) | present `sk-or-…` | adapter มี; tier chain ใช้ openrouter/auto ได้ |
| `provider_keys` (DB) | anthropic=`untested`, openai=`valid`(+1 `invalid`), openrouter=`valid` | DB key store แยกจาก env; anthropic ไม่เคย test |
| `keyword_rules` (DB) | **0 rows** | ไม่มี tenant/project rule → L1 + L3-DB ไม่มีผล |
| `models` table (gateway DB) | **ไม่มี** (`to_regclass` = null) | ไม่มี DB model registry; catalog เป็น static ใน chat-edge `config.go` |

---

## 6. Gap Analysis

### ทำงานจริง (✅)
- chat-edge auto heuristic + model lock — เป็น routing ตัวจริงเพียงตัวเดียว.
- gateway explicit route → provider resolve + region/policy enforce.
- reactive cross-provider fallback (anthropic 401 → openai gpt-4o-mini) — จาก fix ก่อนหน้า, ยืนยันใช้งานได้.

### wired แต่ bypass / ไม่ถึง (⚠️)
- ทั้ง 4 layers ของ gateway (Project Keyword, Semantic, Keyword, Routing-auto) — code ครบ, ทดสอบได้ unit, แต่ runtime ไม่เคยรันเพราะ model ไม่เคยเป็น `"auto"`.
- DEFAULT_RULES / AUTO_DEFAULTS / INTENT_MODEL_MAP — มีอยู่แต่ไม่ถูกเรียก.

### ปิด/ไม่มี (❌)
- Semantic Router ปิด (`=False`).
- `keyword_rules` DB ว่าง, `models` table ไม่มี.
- ไม่มี proactive key-validity filter บน explicit path → เลือก provider ที่ key เสียได้ (พึ่ง reactive fallback).

### Design ↔ Reality gap หลัก
1. **Routing authority อยู่ผิดที่**: ตั้งใจให้ gateway ตัดสิน แต่จริงๆ chat-edge ตัดสินก่อนแล้ว lock → gateway ลดเหลือ executor.
2. **Bypass by construction**: ตราบใดที่ chat-edge ส่ง concrete model, `orchestrator.py:2090` จะ short-circuit เสมอ.
3. **Stale maps + disabled semantic**: แม้จะ "ปลดล็อก" ให้ gateway routing ทำงาน ก็จะได้ model เก่า/ผิด provider จนกว่าจะ update.
4. **Provider selection ไม่รู้สถานะ key**: explicit path เลือก anthropic ทั้งที่ key 401 — แก้ปัญหาด้วย reactive fallback (เพิ่ม latency 1 hop) แทนที่จะ proactive.

---

## 7. Fix Priority (list เท่านั้น — ไม่ implement)

เรียงตาม impact ต่อการทำให้ "smart routing ทำงานจริง":

1. **ตัดสินใจ architecture: ใครเป็นเจ้าของ routing?** (P0, design)
   - ทางเลือก A: ให้ chat-edge ส่ง `"auto"` ผ่านไป gateway (ไม่ resolve เอง) → ปลดล็อก 4 layers. ต้องย้าย/ลบ `resolveAutoIfNeeded` + ปรับ Fix 5 lock (lock ที่ฝั่ง gateway แทน).
   - ทางเลือก B: ยอมรับว่า chat-edge เป็น router → ลบ/ลด smart-routing code ใน gateway (dead code) เพื่อไม่ให้เข้าใจผิด.
   - **ต้องเลือกก่อน** ข้ออื่นถึงมีความหมาย.

2. **Proactive key-valid provider filter บน explicit path** (P1)
   - ให้ `_route_explicit` / orchestrator กรอง provider ที่ `last_test_status='invalid'` หรือ env key เป็น placeholder ออกก่อน เลือก fallback ทันที (ใช้ `providers_with_valid_keys_async` ที่มีอยู่แล้วใน 2056-2062 แต่ตอนนี้ใช้เฉพาะ reasoning path). ลด 1 hop + กัน 401 ที่เห็น.
   - (ถ้าเลือก B ข้อนี้คือ fix ที่ได้ผลจริงที่สุดต่อ UX — ไม่ต้องแตะ chat-edge.)

3. **Update stale model maps** (P1, ทำเมื่อจะใช้ gateway routing)
   - `semantic_router.py:120-122` + `routing_service.py:105-106`: `claude-3-5-sonnet-20241022` → `claude-sonnet-4-20250514`.
   - ลบ/แทน qwen entries ใน INTENT_MODEL_MAP ที่ไม่มี provider/adapter.

4. **เปิด + ทดสอบ Semantic Router** (P2, ถ้าเลือก A)
   - `semantic_router_enabled=True` + ยืนยัน sentence-transformers โหลดได้ใน container + threshold เหมาะสม.

5. **Seed keyword_rules / model registry** (P2/P3)
   - ถ้าจะใช้ project/keyword routing ต้องมี rows ใน `keyword_rules` (ตอนนี้ 0).
   - พิจารณา DB `models` table เป็น single source of truth แทน static catalog แยกใน chat-edge `config.go` กับ gateway.

6. **Test ANTHROPIC key + sync env↔DB** (P1, ops)
   - `provider_keys.anthropic = untested`; env key 401. รัน test-key เพื่อให้ proactive filter (ข้อ 2) มีข้อมูลถูกต้อง.

---

## Appendix — Key file:line index

| หัวข้อ | ตำแหน่ง |
|---|---|
| Gateway bypass gate | `backend/app/services/orchestrator.py:2090-2092` |
| Routing chain invoke | `orchestrator.py:685-695` (`_apply_router_chain`, `select_route`) |
| L1 project keyword | `keyword_router.py:152-195`; call `orchestrator.py:2183-2192` |
| Semantic instantiate / gate | `orchestrator.py:201-207`; `config.py:250` |
| Semantic intent→model map | `semantic_router.py:119-126` (stale ids) |
| L3 keyword default rules | `keyword_router.py:41-83`; call `orchestrator.py:2212-2219` |
| L4 routing auto defaults | `routing_service.py:100-112`; explicit `:187-231`; gate `:183-185` |
| Reactive key check | `orchestrator.py:713-721` |
| Proactive valid-key (reasoning only) | `orchestrator.py:2056-2062`; `provider_key_service.py:278-299` |
| chat-edge auto resolve | `services/chat-edge-api/internal/service/message_service.go:2156-2213` |
| chat-edge heuristic | `internal/service/model_resolver.go:192-278` |
| chat-edge default preference | `model_resolver.go:152-163` (`claude-sonnet-4-20250514`) |
| chat-edge IsAutoModel | `model_resolver.go:27-33` |
| chat-edge catalog | `internal/config/config.go:156-172` |
| outbound model field | `message_service.go:2358` (`GatewayChatRequest.Model`) |
| fallback chain (works) | `backend/app/services/providers/model_tiers.py` (`canonical_model`, `get_fallback_chain`) |

**Live confirmation:** `routing_decision router="explicit" model="claude-sonnet-4-20250514"` — gateway log req_b7bdd564 (2026-05-31T11:27:08Z). `semantic_router_enabled=False`. `keyword_rules`=0 rows. `models` table absent.
</content>
