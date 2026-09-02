# Provider Key Audit

วันที่ตรวจ: 2026-08-31

Branch: `fix/provider-key-audit` (worktree แยกจากงาน FIX34)

ขอบเขต: admin provider-key UI/API, provider adapters, model discovery, DB และ real gateway request

> รายงานนี้ไม่มี API key เต็ม ไม่มี plaintext secret และไม่มีการส่ง key ไปยังปลายทางอื่นนอกจาก provider เจ้าของ key

## สรุปผล

- สาเหตุจริงของ OpenRouter คือ **validate ผ่าน แต่ save ล้ม** ไม่ใช่ key format หรือ network: OpenRouter ตอบ 200 แล้ว PostgreSQL ปฏิเสธ `INSERT` เพราะ `(provider_name, label)=(openrouter, admin)` ชน unique constraint กับแถวเก่าที่ถูก soft-delete
- UI เห็นเพียง `Failed to add key` เพราะ unhandled 500 ตอบ body แบบ generic และไม่มี CORS headers ทำให้ browser อ่าน body ไม่ได้
- แพตช์แก้ให้ re-use แถว inactive ที่ใช้ label เดิม, คืน error แยก `stage=validate|save`, แสดง upstream status/message โดยไม่แสดง key และเพิ่ม validator ให้ registered chat adapters ที่ก่อนหน้านี้ถูกมองเป็น unknown provider
- ยัง **ยิงโมเดลจริงผ่าน gateway ไม่สำเร็จ**: OpenAI key ปัจจุบัน authenticate `/v1/models` ได้ แต่ inference ตอบ 429 `credit_balance_exhausted`; OpenRouter key ที่ผู้ใช้เคยกรอกไม่เคยถูก save จึงไม่มี credential ให้ทดสอบต่อ
- ดังนั้น FIX33/FIX33.1 probes ยัง blocked ด้วย credential/credit ไม่ใช่ด้วย add-key code หลังแพตช์นี้

## STEP 1: หลักฐานอาการจริง

### Network

UI ส่ง payload ถูก schema:

```http
POST http://localhost:8080/admin/providers/openrouter/keys
Content-Type: application/json

{"api_key":"<redacted>","label":"admin"}
```

หลักฐานจากคำขอ OpenRouter ของผู้ใช้เดิม:

- OpenRouter validation: `GET https://openrouter.ai/api/v1/auth/key` ได้ `200 OK`
- Admin endpoint: `POST /admin/providers/openrouter/keys` ได้ `500 Internal Server Error`
- access log ไม่เก็บ response body ไว้ จึง replay collision เดียวกันด้วย active OpenAI key ที่ validate ผ่าน โดยไม่พิมพ์ key ผล body เต็มคือ:

```json
{"error":{"code":"internal_error","message":"An internal service error occurred."},"request_id":"a326e7c9-03d7-4d67-b8f1-0c1a335231ca"}
```

response ดังกล่าวไม่มีทั้ง `Access-Control-Allow-Origin` และ `Access-Control-Allow-Credentials` เมื่อส่ง `Origin: http://localhost:3000`; browser จึงรายงาน fetch failure และ [หน้า UI](../admin-ui/app/providers/page.tsx) เดิม fallback เป็น `Failed to add key`

เพื่อแยก validate failure ออกจาก save failure ได้ทดสอบด้วย credential สังเคราะห์ที่ไม่ใช่ secret:

```text
HTTP 400
{"error":{"code":"key_validation_failed","message":"Key validation failed: User not found."},"test_result":{"valid":false,"latency_ms":337,"error":"User not found."}}
```

กรณีนี้ DB ไม่เพิ่มแถว เป็นหลักฐานว่า route validate ก่อน save

### Backend log

เหตุการณ์เดียวกับคำขอจริงที่ `2026-08-31T08:26:19Z`:

```text
POST /admin/providers/openrouter/keys START
GET https://openrouter.ai/api/v1/auth/key "HTTP/1.1 200 OK"
POST /admin/providers/openrouter/keys status=500
psycopg.errors.UniqueViolation: duplicate key value violates unique constraint "uq_provider_key_label"
DETAIL: Key (provider_name, label)=(openrouter, admin) already exists.
```

อาการเดิมเกิดซ้ำเวลา 08:26, 08:51 และ 08:52 UTC. จุดที่เกี่ยวข้องในโค้ดคือ route [backend/app/api/routes/admin.py:353](../backend/app/api/routes/admin.py), unique constraint [backend/app/models/provider_key.py:26](../backend/app/models/provider_key.py) และ add service [backend/app/services/provider_key_service.py:393](../backend/app/services/provider_key_service.py)

### DB

ไม่พบแถวใหม่จากคำขอที่ fail. สถานะที่อ่านโดยไม่แตะ ciphertext/plaintext:

| provider | label | active | last test | ความหมาย |
|---|---|---:|---|---|
| OpenRouter | `admin` | no | invalid, re-tested 2026-08-31 | ตอนเกิดเหตุแถวนี้ยังมี status valid จาก 2026-05-18 และครอง unique label; live re-test รอบ audit ได้ 401 `Missing Authentication header` |
| OpenRouter | `Imported from environment` | no | untested | placeholder จาก env, dispatch ไม่ใช้ |
| OpenRouter | `Key 3` | no | invalid | แถว inactive |
| OpenAI | `admin` | yes | valid, 2026-08-31 | `/v1/models` ผ่าน แต่ inference ไม่มีเครดิต |
| Sran AI | `mock-provider instrument v2` | yes | valid, 2026-08-31 | internal mock endpoint ไม่ใช่ paid/real model |

OpenRouter มี 3 แถว, แต่ usable/active = 0 จึงแสดง `no key` อย่างถูกต้อง แม้มี row เก่าในตาราง

### ผลตรวจ H1-H7

| สมมติฐาน | ผล | หลักฐาน |
|---|---|---|
| H1 container ไม่มี egress | ไม่จริง | host และ backend container เข้า `https://openrouter.ai/api/v1/key` ได้และรับ 401 เมื่อไม่ส่ง credential; คำขอจริงเคยได้ 200 |
| H2 endpoint/header ผิด | บางส่วน | Bearer header ถูกต้อง; `HTTP-Referer`/`X-OpenRouter-Title` เป็น optional ตาม [quickstart](https://openrouter.ai/docs/quickstart). เปลี่ยน probe จาก legacy `/auth/key` เป็น [endpoint ปัจจุบัน `/api/v1/key`](https://openrouter.ai/docs/api/api-reference/api-keys/get-current-key) |
| H3 regex ไม่รู้จัก `sk-or-v1-*` | ไม่จริง | add route ตรวจเพียง non-empty/trim ไม่มี provider-specific regex และคำขอจริงผ่าน upstream validation |
| H4 key หมดอายุ/ผิด | ไม่จริง ณ เวลาที่กด | OpenRouter ตอบ 200 ให้ key ใหม่ที่ใช้ในเหตุการณ์จริง; key นั้นไม่ถูกเก็บจึง re-test ปัจจุบันไม่ได้. ส่วน key เก่าใน inactive `admin` row ถูก re-test แยกและยืนยันแล้วว่า invalid |
| H5 save/encryption/constraint ล้ม | **จริง** | `uq_provider_key_label` ชนแถว inactive label `admin`; fail หลัง validate และก่อน row ใหม่ถูกเขียน |
| H6 OpenRouter adapter ไม่ register | ไม่จริง | OpenRouter chat adapter อยู่ใน dispatcher |
| H7 UI payload ผิด | ไม่จริง | UI ส่ง `api_key`, `label`, optional `base_url` ตรงกับ backend schema |

## ที่มาของการ์ด provider

การ์ดไม่ใช่ hardcoded list ใน React และไม่ได้มาจาก dispatcher โดยตรง. `GET /admin/providers` รวม provider จาก `_visible_candidates()` ใน model registry ที่ [backend/app/api/routes/admin.py:91](../backend/app/api/routes/admin.py) แล้ว UI ใส่เฉพาะ branding/presentation. ผลคือการ์ดอาจปรากฏแม้ runtime adapter ใช้งานไม่ได้

runtime มี **17 providers รวม Z.AI**; รายชื่อในโจทย์ตก Z.AI ไปหนึ่งชื่อ. `mock` มีจริงแต่ถูกซ่อนจาก admin โดยตั้งใจที่ [backend/app/api/routes/admin.py:88](../backend/app/api/routes/admin.py)

## ตารางความพร้อม 17 providers

คอลัมน์ “รูปแบบ key” คือสิ่งที่โค้ดบังคับจริง: ทุก provider ตรวจแค่ non-empty ไม่มี regex prefix; รายละเอียดในวงเล็บคือ auth transport ไม่ใช่ regex

| provider | adapter มีจริง? | endpoint validate หลังแพตช์ | รูปแบบ key ที่คาดหวัง | เพิ่ม key ได้? | ดึงรายชื่อโมเดลได้? | ยิงโมเดลจริงได้? | หมายเหตุ |
|---|---|---|---|---|---|---|---|
| Anthropic | chat: มี | `GET api.anthropic.com/v1/models` | opaque, `x-api-key` | โค้ดพร้อม; ต้องมี key | static catalog, runtime 9; ไม่พิสูจน์ entitlement | ต้องมี key | เปลี่ยนจาก paid 1-token chat probe เป็น model-list probe |
| Cohere | **ไม่มี production chat adapter** | ปฏิเสธ `not_implemented` | opaque/Bearer; no regex | **ไม่ได้: โค้ดไม่พร้อม** | discovery code มี `/v1/models`, runtime seed 4 | **ไม่ได้: adapter pending** | การ์ดมาจาก registry แต่ stub อยู่ `_pending` |
| DeepSeek | chat: มี | `GET api.deepseek.com/v1/models` | opaque/Bearer | โค้ดพร้อม; ต้องมี key | dynamic, runtime 2 | ต้องมี key | ก่อนแพตช์ add-key ตอบ unknown provider |
| ElevenLabs | media TTS: มี; chat: ไม่มี | `GET api.elevenlabs.io/v1/user` | opaque, `xi-api-key` | โค้ดพร้อม; ต้องมี key | ไม่มี discovery; static config 1 | ต้องมี key และทดสอบ media job | ไม่ใช่ chat provider |
| Google AI | chat: มี | `GET generativelanguage.googleapis.com/v1beta/models` | opaque, `x-goog-api-key` | โค้ดพร้อม; ต้องมี key | **static** legacy list, runtime 7 | ต้องมี key | validator ไม่วาง key ใน query string; discovery ยังไม่ใช้ live Models API |
| Groq | chat: มี | `GET api.groq.com/openai/v1/models` | opaque/Bearer | โค้ดพร้อม; ต้องมี key | dynamic, runtime 9 | ต้องมี key | ก่อนแพตช์ add-key ตอบ unknown provider |
| Kimi | chat: มี | `GET api.moonshot.cn/v1/models` | opaque/Bearer | โค้ดพร้อม; ต้องมี key | static, runtime 5 | ต้องมี key | static list ไม่พิสูจน์ account access |
| Minimax | chat: มี | `GET api.minimax.chat/v1/models` | opaque/Bearer | โค้ดพร้อม; ต้องมี key | static, runtime 3 | ต้องมี key | static list ไม่พิสูจน์ account access |
| Mistral | **ไม่มี production chat adapter** | ปฏิเสธ `not_implemented` | opaque/Bearer; no regex | **ไม่ได้: โค้ดไม่พร้อม** | discovery code มี `/v1/models`, runtime seed 3 | **ไม่ได้: adapter pending** | การ์ดมาจาก registry แต่ stub อยู่ `_pending` |
| OpenAI | chat + media: มี | `GET api.openai.com/v1/models` | opaque/Bearer | พร้อม; active key 1 | dynamic, runtime 72 | **ไม่ได้: provider 429 no credits** | manual re-test ผ่าน 200; ไม่มี automatic periodic key revalidation |
| OpenRouter | chat: มี | `GET openrouter.ai/api/v1/key` | มักเป็น `sk-or-v1-*`, Bearer; no regex | **พร้อมหลังแพตช์**; ต้องกรอก key ซ้ำ | public dynamic list, runtime 12 | ต้องกรอก key ซ้ำ/มี quota | root save bug แก้แล้ว; Referer/Title optional |
| Perplexity | **ไม่มี production chat adapter** | ปฏิเสธ `not_implemented` | opaque/Bearer; no regex | **ไม่ได้: โค้ดไม่พร้อม** | discovery code มี `/models`, runtime seed 3 | **ไม่ได้: adapter pending** | การ์ดมาจาก registry แต่ stub อยู่ `_pending` |
| Qwen | chat: มี | `GET dashscope.aliyuncs.com/compatible-mode/v1/models` | opaque/Bearer | โค้ดพร้อม; ต้องมี key | static, runtime 4 | ต้องมี key | static list ไม่พิสูจน์ account access |
| Replicate | media video/music: มี; chat: ไม่มี | `GET api.replicate.com/v1/account` | token/Bearer; no regex | โค้ดพร้อม; ต้องมี key | ไม่มี discovery; static config 3 | ต้องมี key และ budget แล้วทดสอบ media job | ไม่ใช่ chat completion provider |
| Sran AI | self-hosted chat: มี | `GET {base_url}/models` | opaque/Bearer; endpoint อาจไม่ enforce auth | โค้ดพร้อมเมื่อมี endpoint | dynamic จาก endpoint, runtime 1 | **ยังไม่ได้กับ real model** | configured row ชี้ mock-provider; 200 พิสูจน์ reachability มากกว่าความถูกต้องของ key |
| xAI | chat: มี | `GET api.x.ai/v1/models` | opaque/Bearer | โค้ดพร้อม; ต้องมี key | static, runtime 3 | ต้องมี key | static list ไม่พิสูจน์ account access |
| Z.AI (`zai`) | chat: มี | `GET api.01.ai/v1/models` | opaque/Bearer | โค้ดพร้อม; ต้องมี key | static, runtime 2 | ต้องมี key | adapter/seed ใช้ 01.AI Yi endpoint แต่ UI brand เป็น Z.AI; ต้องให้เจ้าของยืนยัน product mapping |

`mock` เพิ่มเติม: media video adapter มีจริง, ไม่ต้องมี key, model `mock-video-1`, ถูกซ่อนจากหน้า providers และไม่ใช่หลักฐานว่า external LLM ใช้งานได้

Dispatcher ที่ใช้งาน chat จริงมี 12 รายที่ [backend/app/services/provider_dispatcher.py:61](../backend/app/services/provider_dispatcher.py). Cohere/Mistral/Perplexity ถูกเว้นโดยตั้งใจ; ElevenLabs/Replicate ใช้ media resolver แยกต่างหาก. Discovery map อยู่ที่ [backend/app/services/discovery/provider_discovery.py:486](../backend/app/services/discovery/provider_discovery.py)

## สิ่งที่แก้

1. [provider_key_repository.py](../backend/app/repositories/provider_key_repository.py) เพิ่ม lookup โดย `(provider, label)` ที่เห็นทั้ง active/inactive
2. [provider_key_service.py](../backend/app/services/provider_key_service.py) re-activate และเขียนทับ ciphertext ของ inactive slot แทน `INSERT`; active collision คืน 409 conflict; reset test metadata ก่อนบันทึกผลใหม่
3. [admin.py](../backend/app/api/routes/admin.py) แยก response เป็น `stage=validate` และ `stage=save`, map unique conflict, rollback transaction/base-url cache และ log เฉพาะ provider + exception type
4. [api.ts](../admin-ui/lib/api.ts) และ [page.tsx](../admin-ui/app/providers/page.tsx) อ่าน nested JSON/plain-text/status/network error แล้วแสดงเหตุผลแทน generic toast
5. เพิ่ม generic `/models` validation สำหรับ DeepSeek, Groq, Kimi, Minimax, Qwen, xAI, Z.AI; เพิ่ม Google header validator; เปลี่ยน OpenRouter เป็น current `/api/v1/key`; pending adapters ตอบชัดว่าไม่ implemented
6. Error extraction จำกัดข้อความ upstream และ redact credential หาก provider echo กลับ; ไม่มี raw exception text ใน API/log

การเปลี่ยน save behavior ไม่เปลี่ยน encryption scheme, schema หรือ network egress policy

## Tests

รันใน disposable backend container ที่ mount worktree นี้:

```text
pytest tests/test_provider_key_add_flow.py \
       tests/test_key_test_endpoints_a3.py \
       tests/test_provider_key_repository.py -q
28 passed, 8 warnings in 8.29s
```

ครอบคลุม provider 200, 401, timeout, save failure, inactive-label reuse, active conflict, Google header และ pending adapter. UI:

```text
npx tsc --noEmit
npm test -- --run lib/api.test.ts
11 passed
```

targeted Ruff สำหรับ service และ flow tests ผ่าน; `git diff --check` ผ่าน. Broad Ruff ของ legacy files ยังพบ unused imports เดิมใน `admin.py`/repository tests ซึ่งไม่เกี่ยวกับ behavior นี้และไม่ได้ refactor เพิ่ม

## STEP 4: ผลพิสูจน์ปลายทาง

### Add -> DB -> UI

- regression test พิสูจน์ provider ตอบ 200 แล้ว route เขียน DB พร้อม `last_test_status=valid`
- regression test พิสูจน์ label ที่ soft-delete ถูก re-use ด้วย row id เดิม จึงไม่ชน unique constraint
- production stack ที่ `localhost:3000` ยัง bind กับ worktree เดิมซึ่งมี FIX34 ทำงานขนานอยู่ จึงไม่ได้ rebuild ทับ; ต้อง merge/cherry-pick แพตช์ก่อนจึงกรอก OpenRouter key ซ้ำใน UI ได้
- ไม่สามารถนำ OpenRouter key จาก request เดิมกลับมาใช้: save ไม่เคยเกิดขึ้นและระบบไม่ log plaintext ตามกฎความปลอดภัย

### Model list

- OpenAI live re-validation: `GET /v1/models` = 200, latency 1.148s; runtime catalog มี 72 active rows
- OpenRouter public discovery เข้าถึงได้จาก host/container; runtime catalog มี 12 active rows แต่ usable key = 0
- OpenRouter inactive `admin` row ที่ค้าง status valid ตั้งแต่เดือนพฤษภาคมถูก live re-test แล้ว: 401, `Missing Authentication header`, latency 110ms และ status ใน DB ถูกแก้เป็น invalid
- การมี model row หรือ static catalog ไม่เท่ากับ account มีสิทธิ์ inference

### Real chat completion ผ่าน gateway

ทดสอบ `gpt-4o-mini` ผ่าน `POST /v1/chat/completions`:

```text
gateway HTTP 502 provider_error
OpenAI POST /v1/chat/completions -> HTTP 429 (retry 3 ครั้ง)
provider type=insufficient_quota, code=credit_balance_exhausted
fallback Groq/OpenRouter -> skipped: no_api_key
```

ผลสุดท้าย: **ยังไม่มี real model response**. Auth ของ OpenAI ใช้ได้แต่ไม่มี credits; OpenRouter ไม่มี key ที่ persisted. ดังนั้น probe ที่ค้าง **ยังรันไม่ได้** จนกว่าจะทำ owner action ข้อ 1 หรือ 2 ด้านล่าง

เมื่อมี model ที่ยิงจริงผ่านแล้ว ให้กำหนด model id ที่ตรวจแล้วและรันคำสั่งจาก `scripts/probes/long-context/README.md`:

```powershell
$env:PROBE_MODEL="<working-model-id>"
npm --prefix apps/desktop run build
node scripts/probes/long-context/probe.js web 20,50,100,200,400
node scripts/probes/long-context/probe.js desktop 20,50,100,200,400
node scripts/probes/long-context/dlp-check.js
```

อย่าใช้ default `gpt-5.6-terra` จนกว่าจะยืนยันว่า account/provider รองรับ model นั้นจริง

## สิ่งที่เจ้าของต้องทำ เรียงตามความคุ้ม

1. **กรอก OpenRouter key เดิมซ้ำหลัง deploy แพตช์**: เป็นทางที่เร็วที่สุดเพราะ key เคย validate 200 แล้ว. ทดสอบโมเดลราคาต่ำที่มีอยู่ใน catalog ก่อน; OpenRouter มี free router `openrouter/free` ตาม [official docs](https://openrouter.ai/docs/guides/routing/routers/free-router) แต่ runtime catalog ปัจจุบันยังไม่มี id นี้ ต้องเปิด auto-add/เพิ่ม model row โดยเจ้าของก่อนใช้. Free quota มีข้อจำกัดตาม [OpenRouter FAQ](https://openrouter.ai/docs/faq) จึงอาจต้องแบ่ง FIX33 probe หลายวันหรือซื้อ credits
2. **เติม credit ให้ OpenAI account เดิม**: ไม่ต้องเปลี่ยนโค้ดหรือ key และ `/v1/models` ผ่านแล้ว; เป็นทาง operational ที่สั้นที่สุดหากยอมจ่าย
3. **สมัคร Groq free plan แล้วเพิ่ม key**: adapter, validator และ dynamic discovery พร้อม; ตรวจ rate limits ปัจจุบันจาก [Groq docs](https://console.groq.com/docs/rate-limits) ก่อนรัน probe จำนวนมาก
4. **ใช้ Gemini free tier**: provider มี free tier ตาม [Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing) และแพตช์ validate ผ่าน `x-goog-api-key`; แต่ discovery ใน repo ยังเป็น static legacy list จึงควรยืนยัน/แก้ model id ก่อนวัดผล
5. **Cohere/Mistral/Perplexity**: อย่าซื้อ key เพื่อปลดล็อกงานรอบนี้ เพราะ production dispatcher ยังไม่มี adapter

การซื้อ key/credits, การเปิด egress policy ใหม่ และการเปลี่ยน encryption/vault เป็นการตัดสินใจของเจ้าของ; audit นี้ไม่ได้ทำสิ่งเหล่านั้น

## สิ่งที่ตรวจไม่ได้ในรอบนี้

1. OpenRouter add -> DB -> UI -> inference บน production stack หลังแพตช์ เพราะ key จริงจากคำขอเดิมไม่ถูก persist และห้ามกู้จาก log/request
2. Live validation/inference ของ provider ที่ไม่มี key อีก 14+ ราย; ตารางแยกเป็น “ต้องมี key” แทนการเดาผล
3. Account-specific model entitlement ของ static discovery providers เพราะ static rows ไม่เรียก provider API
4. ElevenLabs/Replicate paid media generation เพราะไม่มี key/budget และไม่ใช่ chat completion path
5. Full FIX33/FIX33.1 measurement เพราะ OpenAI inference ตอบ no credits และ fallbacks ไม่มี key
6. Browser UI หลัง rebuild แพตช์ เพราะ stack ที่ port 3000 เป็น worktree เดิมของงานคู่ขนาน; API contract และ UI parser ถูกทดสอบแยกแล้ว
7. Z.AI กับ 01.AI product mapping เพราะ code ปัจจุบันตั้งชื่อ/branding ไม่สอดคล้อง ต้องให้เจ้าของผลิตภัณฑ์ยืนยัน

## Self-check / diff scope

- ไม่มี API key เต็มใน source, test, report หรือ command output ที่เก็บเป็น artifact
- ตอบได้ชัดว่าเหตุการณ์จริงล้มที่ **save** หลัง validate 200
- ตารางครบ 17 runtime providers และแยก code-broken ออกจาก missing-key
- error message ระบุ validate/save, HTTP status, provider message, timeout/connection โดยไม่ echo key
- ไม่มีการแก้ `apps/desktop/**`, `apps/web-chat/**`, compaction/conversation-state หรือ DLP/policy files
- ไม่ push และไม่ commit key จริง

`git diff --stat` (ไม่รวม test file ใหม่ที่ยัง untracked):

```text
 admin-ui/app/providers/page.tsx                  |   4 +-
 admin-ui/lib/api.test.ts                         |  22 +-
 admin-ui/lib/api.ts                              |  19 ++
 backend/app/api/routes/admin.py                  |  65 +++++-
 backend/app/repositories/provider_key_repository.py | 15 ++
 backend/app/services/provider_key_service.py     | 234 ++++++++++++++++-----
 backend/tests/test_key_test_endpoints_a3.py      |   8 +-
 backend/tests/test_provider_key_repository.py    |   4 +-
 8 files changed, 300 insertions(+), 71 deletions(-)
```

ไฟล์ใหม่: `backend/tests/test_provider_key_add_flow.py`
