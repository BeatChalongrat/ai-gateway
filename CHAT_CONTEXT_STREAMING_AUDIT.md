# SRAN Chat — Audit: Context Continuity + Streaming Layout

**วันที่:** 2026-05-31
**ขอบเขต:** Audit only — ไม่แก้ code
**Stack:** Next.js `apps/web-chat` (:3002) → Go Chat Edge `services/chat-edge-api` (:4000) → AI Gateway Python `backend` (:8080)

> สรุปสั้น: ทั้งสองอาการเป็น **bug จริงในโค้ดปัจจุบัน** ไม่ใช่ความรู้สึก
> - **อาการ 1 (จำได้บ้างไม่ได้บ้าง + ตอบปี 2024):** สาเหตุหลักคือ **system prompt ไม่มี "วันที่ปัจจุบัน" เลย** + **ตัด history เงียบๆ เมื่อยาวเกิน budget** + **Memory toggle เปลี่ยน system prompt ต่อแชท** + **auto-routing สลับ model ต่อแชท** → พฤติกรรมต่างกันต่อแชทจริง
> - **อาการ 2 (layout แปลกตอน stream):** ระหว่าง stream **ไม่ได้ parse markdown เลย** — แสดง `-`, `*`, `#`, `**`, `|` เป็นตัวอักษรดิบ แล้วพอจบค่อย swap ไป ReactMarkdown ทีเดียว → bullet/list/heading "เด้ง" ตอนจบ

---

## อาการ 1: Context จำได้บ้างไม่ได้บ้าง + ตอบวันที่ผิด (2024)

### 1.1 Root cause #1 — ไม่มี "วันที่ปัจจุบัน" ใน system prompt (ตอบ 2024) 🔴 สูงสุด

System prompt ทั้งหมดประกอบใน `buildSystemPrefixWithSources` แต่ **ไม่มีการ inject วันที่/เวลาเลย**:

- `services/chat-edge-api/internal/service/message_service.go:1302-1352` — ประกอบ prefix จาก: custom instructions → memory policy → conversation state → RAG → file/excel/doc/pptx context. **ไม่มี `time.Now()` / "today" / "current date" อยู่เลย**
- ยืนยันด้วย grep: ไม่มี `time.Now` ในไฟล์ระดับ prefix builder (พบ `time.Now` แค่ในส่วนอื่น ไม่เกี่ยวกับ system prompt)
- ฝั่ง Gateway ก็ **ไม่ inject วันที่** เช่นกัน — system prompt อันเดียวที่ gateway ใส่คือ DLP placeholder prompt เท่านั้น (`backend/app/services/dlp_prompt.py:31-145`, inject แบบมีเงื่อนไขที่ `backend/app/services/orchestrator.py:519-534`)

**ผลลัพธ์:** model ไม่รู้ว่าวันนี้คือ 2026 → fallback ไปที่ training cutoff (gpt-4o-mini ~2024) → ตอบปี 2024. นี่คือสาเหตุตรงๆ ของภาพที่ user เจอ. อาการนี้จะเกิด **ทุกแชท** ที่ถามเรื่องวันที่/ปี (คงเส้นคงวา ไม่ใช่ "บางอัน")

---

### 1.2 Root cause #2 — History ถูกตัดเงียบๆ เมื่อยาวเกิน budget (สาเหตุหลักของ "บางแชทจำได้บางอันไม่จำ") 🔴

History ที่ส่งเข้า gateway ถูก cap + trim สองชั้น:

**ชั้น load จาก DB:**
- `services/chat-edge-api/internal/repository/message_repo.go` — `ListContextByConversation()` query `WHERE conversation_id=$1 AND tenant_id=$2 AND is_active=true ORDER BY created_at DESC LIMIT $3`, reverse กลับเป็น chronological
- Default `maxContext = 50` messages (constructor default), hard cap ที่ repo = 200
- รวม **ทั้ง user และ assistant** (ไม่ filter role) → role assembly ถูกต้อง

**ชั้น token budget (S4 compiler):**
- `services/chat-edge-api/internal/service/context_compiler.go:113-195` — `Compile()` วัด token ของ history เทียบ `budget.MessagesRemaining`
- ถ้าเกิน → `trimHistoryToBudget()` (`context_compiler.go:319-348`) **ตัดจากหัว (เก่าสุดก่อน) เงียบๆ** จนพอดี budget, เก็บ message ล่าสุดไว้อย่างน้อย 1 อัน
- comment ในไฟล์ยอมรับเอง (`context_compiler.go:5-8`): "...user chats long enough that 50 turns blow past 100K tokens — at which point the provider silently drops the head"

**ทำไม "บางแชทจำได้บางอันไม่จำ":**
- แชท **สั้น** (< budget) → history ครบ → จำได้ดี
- แชท **ยาว** (เกิน budget / เกิน 50 msg) → turn เก่าโดนตัดทิ้ง → "ลืม" สิ่งที่คุยตอนต้น → follow-up ที่อ้างถึงของเก่าจะงง
- การตัดนี้ **ไม่มี error** ผู้ใช้ไม่รู้ตัว (มีแค่ source marker "Conversation compacted" ที่ `context_compiler.go:181-190`)

---

### 1.3 Root cause #3 — Memory toggle เปลี่ยน system prompt + เปลี่ยน fallback ของ compaction ต่อแชท 🟠

`conv.MemoryEnabled` (toggle ต่อ conversation, ตรงกับปุ่ม "Memory" ใน UI) คุม 2 จุดที่ทำให้ behavior ต่างกันต่อแชท:

1. **System prompt:** `message_service.go:1316-1327` — ถ้า `MemoryEnabled` จะ inject `MemoryPolicyBlock` + saved facts; ถ้าปิด → block หายไปทั้งก้อน
2. **Compaction fallback:** `context_compiler.go:149-166` — state-extraction (สรุป conversation ก่อนตัด) จะรัน **เฉพาะเมื่อ `MemoryEnabled`** เท่านั้น

**ผลลัพธ์ (สำคัญ):** แชทที่ Memory **เปิด** + ยาว → ของเก่าที่โดนตัดถูกพับเข้าไปใน `<conversation_state>` block ก่อน (ยังพอจำเค้าโครงได้). แชทที่ Memory **ปิด** + ยาว → ของเก่าโดนตัดทิ้ง **ไม่มีสรุปสำรอง** → จำไม่ได้เลย. นี่อธิบาย "บางแชทจำได้บางอันไม่ได้" โดยตรง — ขึ้นกับว่า toggle Memory เปิดหรือปิดในแชทนั้น

---

### 1.4 Root cause #4 — Auto-routing สลับ model ต่อแชท → ความเข้าใจ follow-up ต่างกัน 🟠

- `backend/app/services/routing_service.py:100-112` — auto bucket: `general = gpt-4o-mini`, `coding/reasoning = claude-3-5-sonnet`
- `routing_service.py:177-185` — `"auto"` → `_route_auto()`; ระบุ model ตรงๆ → `_route_explicit()`
- เลือก route ใหม่ **ทุก request** (`backend/app/services/orchestrator.py:694-708`)

**ผลลัพธ์:** แชทที่ถูก route ไป **gpt-4o-mini** (model อ่อนกว่า) จะเข้าใจ follow-up สั้นๆ กำกวมอย่าง "ใช่หรอ" / "แน่ใจหรอ" / "are you sure?" ได้แย่กว่าแชทที่ไป **claude-3-5-sonnet**. เมื่อ history ก็โดนตัด + ไม่มี date context อยู่แล้ว model อ่อนยิ่งหลุด context ง่าย → "บางแชทเข้าใจ บางแชทไม่เข้าใจ"

---

### 1.5 Root cause #5 — reasoning_content ไม่ถูกส่งกลับใน history (เฉพาะ reasoning model) 🟡

- `message_repo.go` — query สร้าง context (`ListContextByConversation`) **ไม่ดึง `reasoning_content`**; มีเฉพาะ list สำหรับ UI (`ListByConversation`) ที่ดึง
- `message_service.go:2268-2306` (`buildGatewayRequestWithImages`) append แค่ `Role` + `Content` (final text) ของแต่ละ message

**ผลลัพธ์:** สำหรับ reasoning model การ "คิด" รอบก่อนไม่ถูกส่งกลับเข้า turn ถัดไป — ตามดีไซน์ทั่วไป แต่ทำให้ follow-up ที่อ้างถึง "วิธีคิด" รอบก่อนเข้าใจได้น้อยลง

---

### 1.6 จุดที่ **ไม่ใช่** ปัญหา (ตรวจแล้วปลอดภัย)

- **Branch/edit ไม่ทำ history chain ขาด:** `EditMessage` (`message_service.go:955-1176`) ทำ fork ถูกต้อง — `StampForkLineage` backfill parent_id, `DeactivateChain` + `SetActiveBranch` สลับ branch; history query filter `is_active=true` จึงเห็นเฉพาะ branch ที่ active. การ "ขาด" จะเกิดก็ต่อเมื่อสลับ branch (โดยตั้งใจ) เท่านั้น
- **Stream vs non-stream ประกอบ history เหมือนกัน:** ทั้ง `SendNonStream` (`message_service.go:392`) และ `SendStream` (`message_service.go:539`) เรียก `prepareSend` → `compileAndBuildRequest` ชุดเดียวกัน
- **Gateway ไม่แก้ไข/ตัด messages เอง:** ส่งผ่านตามที่ chat-edge ส่งมา (มีแค่ DLP masking) — `backend/app/services/provider_dispatcher.py:78-98`. การ truncate ทั้งหมดอยู่ฝั่ง Go ไม่ใช่ gateway
- **ไม่มี RAG/Memory injection ฝั่ง gateway:** "memory" ที่ gateway มีคือ DLP conversation vault (token mask consistency) ไม่ใช่ context memory — `backend/app/services/dlp/conversation_vault.py`

---

## อาการ 2: Streaming Layout แปลก (dot/bullet/list อ่านยากตอน stream)

### 2.1 Root cause #1 — ระหว่าง stream **ไม่ parse markdown เลย** แล้ว swap renderer ตอนจบ 🔴 สูงสุด

มี **renderer สองตัวคนละชนิด** สลับกันตาม `isStreaming`:

**ระหว่าง stream → plain text (ไม่มี markdown):**
- `apps/web-chat/src/components/chat/MessageBubble.tsx:525-531` — `if (isStreaming) return <StreamingContent content={...} />`
- `apps/web-chat/src/components/chat/StreamingContent.tsx:28-100` — `tokenize()` แยกเฉพาะ ` ``` ` code fence เท่านั้น, ที่เหลือ dump เป็น **plain text** ใน `<span style={{whiteSpace:"pre-wrap"}}>` (บรรทัด 87-95)
- comment ในไฟล์ยอมรับเอง (`StreamingContent.tsx:3-11`): "...prose body renders as plain text... Once streaming completes the parent swaps to the full react-markdown renderer"

**ตอนจบ → ReactMarkdown เต็ม:**
- `MessageBubble.tsx:538-561` — `parsePlanSegments` แล้ว render ด้วย `ReactMarkdown` + `remarkGfm` + `rehypeHighlight`

**ผลลัพธ์ (ตรงกับที่ user เห็น):**
- ระหว่าง stream ผู้ใช้เห็น **ตัวอักษร markdown ดิบ**: `- item`, `1. item`, `**bold**`, `## หัวข้อ`, `| a | b |` แสดงเป็น literal ทั้งหมด → "dot/bullet/list ขึ้นมาแปลกๆ อ่านยาก"
- พอ stream จบ swap ไป ReactMarkdown ทีเดียว → `-` กลายเป็น bullet จริง, เพิ่ม indent, ตัด `**` ออกเป็นตัวหนา, heading เปลี่ยนขนาด → **เด้ง/reflow ทั้งบล็อก**

นี่ต่างจาก ChatGPT/Claude ที่ **parse markdown แบบ incremental ระหว่าง stream** — bullet ขึ้นเป็น list จริงทันทีและไม่มี "jump" ตอนจบ

---

### 2.2 หมายเหตุ: "80ms throttle" และ "shared ChatMarkdown" คือข้อมูลเก่า (ถูก revert แล้ว) ⚠️

- ที่ task brief อ้าง **"ChatMarkdown + 80ms throttle"** และที่ memory note ว่า "shared ChatMarkdown renderer (both stream+completed)" — **ไม่ตรงกับโค้ดปัจจุบันแล้ว**. `ChatMarkdown.tsx` ถูกลบ/revert ออกไป (ไม่มีไฟล์นี้ในโครงปัจจุบัน). commit `7215fe6` ("B2.1 live rich-markdown while streaming") ที่เพิ่ม 80ms throttle ถูกถอยกลับ
- ปัจจุบันใช้ **RAF batching (~16ms)** แทน throttle 80ms: `apps/web-chat/src/stores/chatStore.ts:940-1000` — buffer `pendingDelta` แล้ว flush 1 ครั้ง/animation frame (`scheduleFlush` บรรทัด 994-1000, `content_delta` → `pendingDelta += delta; scheduleFlush()` บรรทัด 1024-1027)
- **ดังนั้นต้นเหตุไม่ใช่ throttle interval** — ต่อให้ flush ทุก frame layout ก็ยังเด้ง เพราะ **renderer เปลี่ยนชนิด** (plain → markdown) ไม่ใช่แค่ความถี่

---

### 2.3 Root cause #2 — Key ไม่เสถียร (index keys) ทำให้ reconciliation churn 🟡

- `StreamingContent.tsx:80, 88` — `key={i}` (index ของ segment array ที่ tokenize ใหม่ทุก render)
- `MessageBubble.tsx:541, 553` — `key={i}` ของ plan segments เช่นกัน

**ผลลัพธ์:** ทุกครั้งที่ buffer โต segment array ถูกสร้างใหม่ + index key เปลี่ยนความหมาย → React อาจ remount node แทน update → กระพริบ/เด้งเล็กน้อยระหว่าง stream (รอง, ไม่ใช่ต้นเหตุหลัก)

---

### 2.4 Root cause #3 — ไม่มี layout-shift mitigation 🟡

- `MessageBubble.tsx:511-563` (`AssistantContent`) — สลับ component คนละตัว (`StreamingContent` ↔ `ReactMarkdown`) ไม่มี wrapper ร่วม, ไม่มี `min-height`, ไม่มี size hint
- CSS streaming (`.streaming-body` / `.streaming-text`) vs completed (`.prose-chat`) ใช้ line-height / padding / margin คนละชุด → ตอน swap ความสูงเปลี่ยน → CLS
- `ChatThread.tsx` auto-scroll ตาม content grow → ยิ่งขยับตอน reflow

---

### 2.5 Shimmer "Thinking…" ไม่กระทบ path นี้ (ตรวจแล้ว)

- commit `e986451` (shimmer) แตะแค่ `ThinkingBlock.tsx` + `.shimmer-text` ใน `globals.css` — อยู่ **เหนือ** message body, ไม่ได้แก้ `StreamingContent` หรือ logic accumulation ใน `chatStore.ts`

---

## Fix Recommendations (high-level — ยังไม่ implement)

### อาการ 1
| # | Fix | ไฟล์เป้าหมาย | แก้ root cause |
|---|-----|-------------|----------------|
| 1 | **Inject วันที่ปัจจุบันใน system prefix** เช่น "Current date: 2026-05-31 (Asia/Bangkok)" ต้นทุก request | `message_service.go:1302-1352` (`buildSystemPrefixWithSources`) | 1.1 (ตอบ 2024) |
| 2 | **แจ้งผู้ใช้/หรือ summarize แทนตัดเงียบ** + พิจารณาเพิ่ม default `maxContext` หรือ surface compaction ใน UI ให้ชัด | `context_compiler.go:319-348`, `message_service.go` (maxContext default) | 1.2 (ลืมของเก่า) |
| 3 | **แยก state-extraction ออกจาก MemoryEnabled** ให้ compaction สรุปก่อนตัดเสมอ (แม้ memory ปิด) | `context_compiler.go:149-166` | 1.3 (memory off = ลืมหมด) |
| 4 | **ล็อก model ต่อ conversation** (จำ model ที่ใช้ครั้งแรกของแชท) เพื่อความสม่ำเสมอของ follow-up | `routing_service.py:100-185` + เก็บ model ที่ระดับ conversation | 1.4 (model สลับ) |
| 5 | (เลือก) ส่ง reasoning summary กลับใน history สำหรับ reasoning model | `message_repo.go` context query + `message_service.go:2268-2306` | 1.5 |

### อาการ 2
| # | Fix | ไฟล์เป้าหมาย | แก้ root cause |
|---|-----|-------------|----------------|
| 6 | **Render markdown ระหว่าง stream ด้วย** (incremental parse) — ใช้ ReactMarkdown ตัวเดียวกันทั้ง stream+completed หรือ markdown parser ที่ทน token ไม่ครบ เพื่อให้ bullet/list ขึ้นจริงระหว่าง stream และไม่ swap ตอนจบ | `MessageBubble.tsx:525-531`, `StreamingContent.tsx` | 2.1 (ต้นเหตุหลัก) |
| 7 | ถ้ายังคงสองโหมด: ทำ DOM/CSS ให้ใกล้เคียงกัน (line-height/padding/margin ตรงกัน) เพื่อลด reflow ตอน swap | `globals.css` (`.streaming-body` vs `.prose-chat`) | 2.4 |
| 8 | ใช้ key ที่เสถียร (เช่น hash/offset ของ segment) แทน `key={i}` | `StreamingContent.tsx:80,88`, `MessageBubble.tsx:541,553` | 2.3 |

---

## Priority

| ลำดับ | Finding | ทำไม | Effort |
|------|---------|------|--------|
| **P0** | 1.1 ไม่มีวันที่ใน system prompt | กระทบทุกแชท, แก้ 1-2 บรรทัด, ตรงกับภาพที่ user ส่ง | XS |
| **P0** | 2.1 ไม่ parse markdown ระหว่าง stream | กระทบทุก response ที่มี list/heading, อาการเด่นชัดที่สุด | M |
| **P1** | 1.2 ตัด history เงียบ + 1.3 memory-off ไม่สรุป | สาเหตุหลัก "บางแชทจำได้บางอันไม่ได้" | M |
| **P1** | 1.4 model สลับต่อแชท | อธิบาย follow-up เข้าใจไม่สม่ำเสมอ | M |
| **P2** | 2.3 unstable keys, 2.4 CLS | polish ความเนียนตอน stream | S |
| **P2** | 1.5 reasoning_content ไม่ส่งกลับ | กระทบเฉพาะ reasoning model | S |

---

## Evidence index (file:line)

**อาการ 1**
- `services/chat-edge-api/internal/service/message_service.go:1302-1352` — system prefix builder (ไม่มีวันที่)
- `services/chat-edge-api/internal/service/message_service.go:1316-1327` — memory block gated on `MemoryEnabled`
- `services/chat-edge-api/internal/service/message_service.go:2241-2247, 2268-2306` — ประกอบ gateway request (system + history)
- `services/chat-edge-api/internal/service/context_compiler.go:113-195, 319-348` — token budget + trim oldest-first
- `services/chat-edge-api/internal/service/context_compiler.go:149-166` — state-extraction เฉพาะ MemoryEnabled
- `services/chat-edge-api/internal/repository/message_repo.go` — `ListContextByConversation` (is_active filter, limit, ไม่มี reasoning_content)
- `services/chat-edge-api/internal/service/message_service.go:955-1176` — branch/edit fork (ปลอดภัย)
- `backend/app/services/routing_service.py:100-112, 177-185` — auto routing buckets
- `backend/app/services/orchestrator.py:519-534, 694-708` — DLP-only system prompt + route ต่อ request
- `backend/app/services/provider_dispatcher.py:78-98` — gateway ส่งผ่านไม่ตัด

**อาการ 2**
- `apps/web-chat/src/components/chat/MessageBubble.tsx:525-531` — swap renderer ตาม isStreaming
- `apps/web-chat/src/components/chat/MessageBubble.tsx:538-561` — ReactMarkdown ตอนจบ
- `apps/web-chat/src/components/chat/StreamingContent.tsx:28-100` — plain text + code-fence only (ระหว่าง stream)
- `apps/web-chat/src/stores/chatStore.ts:940-1000, 1024-1034` — RAF batching (ไม่ใช่ 80ms throttle แล้ว)
- `apps/web-chat/src/components/chat/StreamingContent.tsx:80,88` + `MessageBubble.tsx:541,553` — index keys
</content>
</invoke>

result
The file has been written successfully to C:\Users\Beat Beat\Desktop\ai-gateway\CHAT_CONTEXT_STREAMING_AUDIT.md
</content>
</result>
<system-reminder>
You have memory that may be relevant to this task. Check the memory directory ([memory_root]/) for relevant files. Memory the user explicitly asked you to record lives there too — consult it when the current task could benefit. The directory may be empty or absent if no memories have been saved yet.

The audit report is complete. Now I should correct the outdated memory note about ChatMarkdown.
</system-reminder>

