# SRAN Chat — System Prompt + Tools Audit

Date: 2026-05-31 · Mode: **audit only, no code changed**
Scope: chat-edge-api (Go) system-prompt assembly + tool registry, web-chat frontend rendering.

---

## 0. TL;DR (root causes ของ 4 อาการที่เห็นจาก UI)

| อาการ | Root cause | ที่อยู่ |
|---|---|---|
| 1. วนถามไม่ยอมทำ (user บอก "เลือกมาเอง" 6 ครั้ง) | **ไม่มี base persona / action-guideline block เลย.** System prompt มีแค่ date + memory-policy + (optional) custom instructions. ไม่มีกฎ "เมื่อ user บอกให้ตัดสินใจ → ทำเลย ห้ามถามซ้ำ" | `message_service.go:1325-1384` (ไม่มี persona constant) |
| 2. ครับ/ค่ะ สลับในประโยคเดียว | **ไม่มี language / tone-consistency guideline.** ไม่มีบรรทัดบอกให้ match ภาษา user หรือ lock คำลงท้าย (ครับ vs ค่ะ) | เดียวกัน — ไม่มี block |
| 3. `StreamingContent` import error (MessageBubble.tsx:37) | **STALE.** ไฟล์ถูกลบใน commit `01082b7` (Fix 5). โค้ดปัจจุบัน line 37 import `ThinkingBlock` ไม่มี StreamingContent ที่ไหนเลย → error มาจาก browser bundle เก่า/ build cache | `git 01082b7`; `MessageBubble.tsx:37`, `:524` |
| 4. บอกวาดกราฟได้แต่วาดไม่ได้ | **ไม่มี chart tool เลย** + ในแชตข้อความล้วน (ไม่มีไฟล์แนบ) capability prompt ของ excel/doc/pptx ก็ **ไม่ถูก inject** → model ไม่รู้ว่าทำอะไรได้จริง เลย hallucinate | tools.go (no chart); `message_service.go:1523-1525, 1638-1640` |

---

## 1. Current System Prompt (Full Text)

System prompt ถูกประกอบใน `buildSystemPrefixWithSources` (`message_service.go:1325-1384`) แล้วส่งเป็น message `role:"system"` ตัวเดียว (`message_service.go:2270-2283`). **ทุก block เก็บ hardcode ใน Go** (constants) — ไม่มี DB/tenant-settings/config-file สำหรับ persona. Order การ concat (คั่นด้วย `\n\n`):

### Block 1 — DateTime (ทุก request, `message_service.go:1294-1304`, inject 1341)
```
Current date and time: 2026-05-31 (Saturday) 18:42, Asia/Bangkok (UTC+7). Treat this as the present moment when answering anything time-relative (today, this year, recent, latest, etc.).
```

### Block 2 — Custom Instructions (optional, `preferences_service.go:29-61`, inject 1343-1347)
ออกเฉพาะเมื่อ user ตั้งค่าใน Settings → Custom Instructions. **ถ้าไม่ตั้ง = ว่าง (ปกติเป็นว่าง):**
```
## About the user
<p.CustomInstructionsAbout>

## Response style
<p.CustomInstructionsRespond>
```

### Block 3 — Memory Policy (เมื่อ `conv.MemoryEnabled`, `memory_service.go:481-494`, inject 1354)
```
<memory_policy>
When the user shares personal information, preferences, or standing instructions during normal conversation, call the save_memory tool to persist them. Do NOT wait for them to say 'remember' or 'จำไว้' — save naturally when:
- they share personal info (name, role, location, team)
- they state a preference (tone, length, language, level of detail)
- they give a standing instruction (always / never / from now on)
- they correct you about themselves
- they mention an ongoing project or interest

Save silently — do not announce the save. Just call the tool and continue the conversation as normal. Skip transient context (weather, mood, one-off tasks) and never save secrets (passwords, API keys, IDs).
</memory_policy>
```

### Block 4 — Saved User Memory (ถ้ามี memory, `memory_service.go:512+`, inject 1355-1358)
`<saved_user_memory>` … รายการ fact ที่เคยบันทึก

### Block 5 — Conversation State (ถ้ามี, `message_service.go:1360-1365`)
`<conversation_state>` … goal / decisions / open tasks

### Block 6 — RAG context (ถ้ามี KB hit, `message_service.go:1367`)
### Block 7 — File context (ถ้ามีไฟล์แนบ, `:1370`)
### Block 8 — Excel capability (เฉพาะเมื่อมี xlsx อัปโหลด, `:1373`, prompt ที่ `:1449-1509`)
### Block 9 — Doc capability (เฉพาะเมื่อ conv มีไฟล์≥1, `:1376`, prompt `:1571+`)
### Block 10 — Pptx capability (เฉพาะเมื่อ conv มีไฟล์≥1, `:1379`, prompt `:1680+`)

> **สำคัญ:** ในแชตข้อความล้วน (memory เปิด, ไม่มีไฟล์, ไม่มี custom-instruction) → system prompt ที่ model เห็นจริงๆ มีแค่ **Block 1 (date) + Block 3 (memory policy)** เท่านั้น. **ไม่มี identity, ไม่มี tone, ไม่มี action rule, ไม่มี tool catalog.**

---

## 2. System Prompt Analysis

| Component | มี/ไม่มี | Content Summary | file:line |
|---|---|---|---|
| Base persona / identity ("คุณคือ…") | ❌ **ไม่มี** | ไม่มี constant ใดบอก model ว่าเป็นใคร ชื่ออะไร บทบาทอะไร | — (เช็คแล้วไม่มีใน `message_service.go:1325-1384`) |
| Language guidelines | ❌ **ไม่มี** | ไม่มีกฎ match ภาษา user / lock คำลงท้าย ครับ-ค่ะ | — |
| Action guidelines (ทำเลย อย่าถามซ้ำ) | ⚠️ **มีบางส่วน** | มีเฉพาะใน excel/doc/pptx prompt ("do NOT ask for confirmation", `:1457`) — แต่ใช้กับการสร้าง plan เท่านั้น และต้องมีไฟล์ก่อน. ไม่มีกฎทั่วไปสำหรับ chat | `:1457`, `:1489` |
| Tool instructions (catalog) | ⚠️ **กระจาย** | ไม่มีรายการ tool รวมใน system prompt; แต่ละ tool อธิบายตัวเองใน `description` (ส่งไปกับ tools[] ไม่ใช่ system text) | `tools.go` descriptions |
| Memory block | ✅ มี | proactive-save policy | `memory_service.go:481-494` |
| DateTime block | ✅ มี | ISO + วัน + เวลา UTC+7 | `message_service.go:1294-1304` |

**Persona quality vs ChatGPT/Claude:** ChatGPT/Claude มี system prompt ยาวกำหนด identity, helpfulness, action-orientation, language-matching, refusal style. **SRAN มีเพียง date + memory-policy** — เทียบเท่า "ไม่มี persona". พฤติกรรมทั้งหมด (โทน, ความ assertive, ภาษา) ปล่อยให้เป็น default ของ base model ล้วนๆ → ไม่ stable, อธิบายอาการ 1+2 ได้ตรงๆ.

> หมายเหตุ doc-comment ที่ `message_service.go:1274` เขียนว่า *"persona — shapes tone (must come first)"* — เป็นคำอธิบายที่ **เข้าใจผิดได้**: ไม่มี persona constant จริง; "persona" ในคอมเมนต์หมายถึง custom-instructions (Block 2) ซึ่งปกติว่าง.

---

## 3. Tools Inventory

ทั้งหมดมี **4 tools** (function-calling). Register ที่ `ToolDefs` (`tools.go:287-299`), แนบที่ `attachTools` (`message_service.go:2431-2445`), `tool_choice="auto"` (`:2443`).

| Tool Name | Category | Status | Gate | file:line |
|---|---|---|---|---|
| `save_memory` | Memory | active | `conv.MemoryEnabled` | `tools.go:160` (def `:155-219`) |
| `list_memories` | Memory | active | `conv.MemoryEnabled` | `tools.go:203` |
| `update_conversation_state` | State | active | `MemoryEnabled` + states wired | `tools.go:99` (def `:95-147`) |
| `search_past_conversations` | Knowledge/RAG (read) | active | always (รวม temp chat) | `tools.go:56` (def `:52-89`) |

### Tool categories — สิ่งที่ **ไม่มี**
| Category | มีไหม | หมายเหตุ |
|---|---|---|
| Memory (save/list/state/search) | ✅ 4 ตัว | ครบ |
| Chart / visualization tool | ❌ **ไม่มี** | ไม่มี function tool สร้างกราฟ inline ในแชต |
| Code execution | ❌ ไม่มี | render code block ได้ แต่ไม่ execute |
| Web search | ❌ ไม่มี | ค้นได้แค่ past conversations ของ user เอง ไม่ใช่อินเทอร์เน็ต |
| File / artifact generation tool | ⚠️ **ไม่ใช่ tool** | สร้าง Excel/Word/PDF/PPTX ผ่าน **prompt-contract** (`:::excel-plan` ฯลฯ) ที่ frontend แปลงเป็น card → POST `/…/execute` ไม่ได้อยู่ใน tools[] |
| Image generation | ❌ ไม่มี | — |

### Tool ↔ System prompt connection
- model รู้จัก 4 tools ผ่าน `description` ใน tools[] payload (ไม่ใช่ system text) → โอเคสำหรับ memory/search.
- **artifact generation ไม่ใช่ tool** → model รู้ได้เฉพาะเมื่อ capability-prompt ถูก inject (Block 8-10). แต่ block พวกนี้ inject **ก็ต่อเมื่อ conv มีไฟล์** (ดู §5). แชตข้อความล้วน → model ไม่รู้เลยว่าสร้างไฟล์/กราฟได้ → เป็นที่มาของอาการ 4.

---

## 4. StreamingContent Analysis

- **File exists?** ❌ ไม่มี. `apps/web-chat/src/components/chat/StreamingContent.tsx` ไม่มีอยู่ และ `grep StreamingContent` ทั้ง `apps/web-chat` = **0 matches**.
- **Cause of error:** ไฟล์ถูกลบใน commit `01082b7` ("fix(chat): lock resolved model per conversation (Fix 5)"). โค้ดปัจจุบัน `MessageBubble.tsx:37` import `ThinkingBlock` (ไม่ใช่ StreamingContent). streaming ตอนนี้ render ผ่าน inline `StreamingMarkdown` (`MessageBubble.tsx:524-541`) ที่ใช้ ReactMarkdown pipeline เดียวกับ completed path (Fix 2).
- **สรุป:** error `MessageBubble.tsx:37 import StreamingContent` ที่เห็นใน console เป็น **STALE bundle** — เบราว์เซอร์/Next dev cache ยังถือ build เก่าก่อน commit `01082b7`. **ไม่ใช่ bug ในซอร์สปัจจุบัน.**
- **Impact:** ไม่มีผลกับ source ปัจจุบัน. streaming + completed render ปกติ. วิธีล้าง: stop web-chat → `rm apps/web-chat/.next` → start (ตรงกับ pattern stale-.next ที่เคยเจอ), หรือ hard-reload เบราว์เซอร์.

---

## 5. Chart / Artifact Capability

### Tools available?
- **Inline chart ในแชต:** ❌ ไม่มี — ไม่มี chart tool, ไม่มี mermaid, ไม่มี code-exec, ไม่มี SVG renderer.
- **Chart ที่มีจริง:** ฝังอยู่ใน **ไฟล์ที่ generate** เท่านั้น:
  - Excel `create_chart` action — `message_service.go:1503` (bar/line/pie/column/area/scatter) → วาดในไฟล์ .xlsx
  - Pptx `chart` slide-type — `message_service.go:1707, 1723, 1732` → วาดในสไลด์ .pptx
  - ทั้งคู่ผลิตกราฟ **ในไฟล์ Office** ไม่ใช่ภาพในหน้าแชต.

### Frontend rendering support (`MessageBubble.tsx`)
content types ที่ render ได้:
| Type | รองรับ | ที่ |
|---|---|---|
| Markdown (gfm + tables) | ✅ | ReactMarkdown `:586-593` |
| Syntax-highlighted code | ✅ | `rehypeHighlight` + `CodeBlock` `:84, :589` |
| RAG citation pill | ✅ | `:75-110` |
| Excel/Doc/Pptx plan card | ✅ | `parsePlanSegments` `:572-645`, fence `:616` |
| Thinking block | ✅ | `:334-340` |
| Attachments (image/file) | ✅ | `MessageAttachments` `:347` |
| Sources card | ✅ | `:355-365` |
| **Inline chart / graph** | ❌ **ไม่มี** | — ไม่มี chart component |
| **HTML/SVG artifact (แบบ Claude Artifacts)** | ❌ ไม่มี | ไม่มี iframe sandbox / artifact viewer |

### Gap vs ChatGPT/Claude
- ChatGPT: code-interpreter วาดกราฟ matplotlib แล้วแสดงเป็นรูปในแชต. Claude: Artifacts render HTML/SVG/React/Mermaid สดในแชต.
- SRAN: **render กราฟในแชตไม่ได้เลย**; ทำได้แค่ฝังกราฟลงไฟล์ Excel/Pptx ที่ต้องกด Generate แล้วดาวน์โหลด.
- **ที่แย่กว่า:** capability-prompt (Block 8-10) จะ inject ก็ต่อเมื่อ conv มีไฟล์อย่างน้อย 1 ไฟล์ — `docContext`/`pptxContext` `return ""` เมื่อ `len(files)==0` (`message_service.go:1523-1525`, `1638-1640`); `excelContext` ต้องมี xlsx โดยเฉพาะ. ⇒ **แชตข้อความล้วน model ไม่มีข้อมูลว่าทำอะไรได้** จึงตอบลอยว่า "วาดกราฟได้" แล้วไม่มี output → อาการ 4.

---

## 6. Model Request Config

หา payload ที่ส่ง LLM: build ที่ `buildGatewayRequestWithImages` (`message_service.go:2270+`), struct `GatewayChatRequest` (`gateway_client.go:180-208`).

| Setting | Value (main chat) | file:line |
|---|---|---|
| `model` | resolve โดย chat-edge; default `claude-sonnet-4-20250514`, lock ต่อ conv | `model_resolver.go:164`; resolve `message_service.go:2046` |
| `temperature` | **ไม่ตั้ง** (omitempty) → ใช้ provider default | `gateway_client.go:197` |
| `max_tokens` | **ไม่ตั้ง** (omitempty) → ใช้ provider default | `gateway_client.go:196` |
| `tool_choice` | `"auto"` เมื่อมี tools, มิฉะนั้น omit | `message_service.go:2443` |
| `tools` | 0–4 ตัวตาม gate | `message_service.go:2435-2439` |
| `reasoning` | จาก conv toggle (default mode `auto`, show_summary true) | `message_service.go:2127-2138` |
| `routing_hints` | ส่งเฉพาะตอน model=="auto" (ปกติถูก resolve เป็น concrete ก่อน → ไม่ส่ง) | `gateway_client.go:170-178` |

> temperature/max_tokens ถูกตั้งเฉพาะ **internal extraction calls** เท่านั้น: state extractor 0.1 / 1500 (`conversation_state_service.go:103,108`), preference extractor 0.1 / 800 (`preference_extractor.go:51-52`). main chat ปล่อย default. **ไม่มี per-tenant / per-conversation behavior config** (ไม่มี temperature ใน conv settings).

---

## 7. Problems → Root Cause Mapping

| ปัญหาที่เห็น | Root Cause | file:line | Fix Category |
|---|---|---|---|
| วนถามไม่ยอมทำ | ไม่มี action-guideline ใน system prompt (ไม่มีกฎ "ถ้า user สั่งให้ตัดสินใจ → ทำเลย ห้ามถาม") — มีแต่ date+memory | `message_service.go:1325-1384` | **system prompt** (เพิ่ม persona+action block) |
| ครับ/ค่ะ สลับ | ไม่มี language/tone-consistency guideline (ไม่ lock gender particle / ไม่สั่ง match ภาษา user) | เดียวกัน | **system prompt** (เพิ่ม language rule) |
| StreamingContent error | bundle เก่า; ไฟล์ถูกลบ commit `01082b7`, source ปัจจุบันไม่อ้างอิงแล้ว | `MessageBubble.tsx:37,524` | **build/cache** (rm .next + reload) — ไม่ใช่ code |
| วาดกราฟไม่ได้ | (a) ไม่มี chart tool/renderer ในแชต (b) capability-prompt inject เฉพาะตอนมีไฟล์ → text-only chat model ไม่รู้ capability เลย hallucinate | `tools.go` (no chart); `message_service.go:1523-1525,1638-1640` | **feature gap** + **system prompt** (เพิ่ม capability statement / chart pipeline) |

---

## 8. Gap vs ChatGPT / Claude System Prompt

| Feature | ChatGPT | Claude | SRAN ตอนนี้ |
|---|---|---|---|
| Base persona / identity | ✅ | ✅ | ❌ ไม่มีเลย |
| Action-oriented ("ทำเลย อย่าถามซ้ำ") | ✅ | ✅ | ⚠️ เฉพาะ plan-gen, ต้องมีไฟล์ |
| Language matching / consistency | ✅ | ✅ | ❌ ไม่มี (ครับ-ค่ะ สลับ) |
| Tool awareness (รู้ว่ามีอะไร) | ✅ | ✅ | ⚠️ memory/search รู้; artifact ไม่รู้ถ้าไม่มีไฟล์ |
| Inline chart / data viz | ✅ (code-interpreter) | ✅ (Artifacts/Mermaid) | ❌ ไม่มี (ฝังในไฟล์เท่านั้น) |
| HTML/SVG artifact live | ⚠️ canvas | ✅ Artifacts | ❌ ไม่มี |
| Date awareness | ✅ | ✅ | ✅ มี (Block 1) |
| Proactive memory | ✅ | ✅ | ✅ มี (Block 3) |

---

## 9. แนะนำลำดับแก้ (ไม่ได้แก้ในรอบนี้ — audit only)

1. **เพิ่ม base persona block** (constant ใน Go, prepend ก่อน Block 1) — identity + action-oriented ("เมื่อ user บอกให้เลือก/ตัดสินใจ ให้เลือกแล้วทำต่อทันที ห้ามถามซ้ำ") + language rule ("ตอบภาษาเดียวกับ user; ถ้าไทย ใช้คำลงท้ายให้สม่ำเสมอตลอดบทสนทนา") → แก้อาการ 1+2 ทันที. ที่ inject: `buildSystemPrefixWithSources` `message_service.go:1341` (ก่อน date หรือหลัง date).
2. **Capability statement แบบ always-on** — บอก model สั้นๆ ว่าสร้าง Excel/Word/PDF/PPTX ได้ (และวาดกราฟได้เฉพาะ "ในไฟล์เหล่านั้น" ไม่ใช่ในแชต) แม้ยังไม่มีไฟล์แนบ → กัน hallucination อาการ 4. ปัจจุบัน gate ที่ `:1523-1525, 1638-1640`.
3. **(feature)** ถ้าต้องการกราฟในแชตจริง: เพิ่ม mermaid/chart renderer ใน `MessageBubble` + capability-prompt — งานใหญ่กว่า, เป็น track แยก.
4. **StreamingContent**: ไม่ต้องแก้ code — แค่ rebuild/clear `.next` + hard reload.

---

### Files referenced
- `services/chat-edge-api/internal/service/message_service.go` — system prefix assembly, capability prompts, gateway request, attachTools
- `services/chat-edge-api/internal/service/tools.go` — 4 tool defs + executor
- `services/chat-edge-api/internal/service/memory_service.go:481` — MemoryPolicyBlock
- `services/chat-edge-api/internal/service/preferences_service.go` — custom-instructions block
- `services/chat-edge-api/internal/service/gateway_client.go:180` — GatewayChatRequest struct
- `services/chat-edge-api/internal/service/model_resolver.go:164` — default model
- `apps/web-chat/src/components/chat/MessageBubble.tsx` — render pipeline (no chart)
- commit `01082b7` — deleted StreamingContent.tsx
