# FIX41 Part A — สำรวจและวัดของเดิม (ก่อนแตะโค้ด)

สาขา `fix41/durable-run` แตกจาก `main` = `6445210`.
เอกสารนี้เขียนก่อนแก้โค้ดบรรทัดแรก ตัวเลขทุกตัวมาจากการรันจริง ไม่ได้อ่านจากรูปทรงของโค้ด

---

## A0. ของที่พบว่า "มีอยู่แล้ว" และตัดออกจากขอบเขต

| สิ่งที่สเปกสั่งให้ทำ | สภาพจริง | การตัดสิน |
|---|---|---|
| desktop: `seq` ต่อ event | **มีแล้ว** `turn-coordinator.ts:841` (`const seq = ++log.seq`) | ไม่สร้างใหม่ |
| desktop: replay ด้วย `lastSeq` | **มีแล้ว** `turn-coordinator.ts:344-351` | ไม่สร้างใหม่ |
| page: cursor + dedupe ต่อ seq | **มีแล้ว** `apps/web-chat/src/lib/bridge.ts:186-215` | ไม่สร้างใหม่ |
| **D3 — บั๊ก "seq รีเซ็ตแล้ว frame ของ turn ที่ 2 ถูกทิ้ง"** | **ไม่มีบั๊กนี้** — มีการ์ด 2 ชั้นที่กันไว้อยู่แล้ว (ดู A4) | รายงานตามจริง ยังเขียน test พิสูจน์ตามที่สเปกสั่ง |
| chat-edge: resume endpoint | **มีแล้ว** (Phase 5) แต่เป็น snapshot ที่พับแล้ว ไม่มีเลขลำดับ | ต่อยอด ไม่เขียนใหม่ |
| Redis เป็น dependency | **มีแล้ว** `go-redis/v9 v9.7.0` ใช้ใน ratelimit/router/sandbox | ไม่เพิ่ม dep |

> **ข้อโต้แย้งต่อสเปก (ข้อ 7 ของกติกา):** สเปกบอกว่า `writeSSEEvent` มี "~22 call site"
> ของจริงคือ **46 call site** ใน `message.go` ไฟล์เดียว (ชนิด event ที่ต่างกัน 19 ชนิด)
> ตัวเลข 22 น่าจะนับ *ชนิด* ไม่ใช่ *จุดเรียก* — ไม่กระทบการออกแบบ (C1 ยังคงแก้ที่จุดเดียว)
> แต่บันทึกไว้เพราะสเปกบอกว่า "ถ้าต้องแก้ call site เกิน 3 ที่ แปลว่าออกแบบผิด"

---

## A1. ตารางสำรวจ event vocabulary

### ฝั่ง chat-edge (Go) — ปล่อยผ่าน `writeSSEEvent` ทั้งหมด 46 จุด, 19 ชนิด

| event | ปล่อยจาก (message.go) | payload | web รับที่ | desktop ปล่อยชนิดนี้ไหม |
|---|---|---|---|---|
| `message_start` | 671, 957, 1142, 1225 | `message_id`, `conversation_id`, `user_message_id?` | chatStore 2003 | ✔ (860) |
| `content_delta` | 521, 1079 | `delta` | ✔ | ✔ (1075) |
| `reasoning_delta` | 530, 1037 | `delta` | ✔ | ✔ (1079) |
| `analysis_status` | 535, 1034, 1040 | `status` | ✔ | ✔ (1146) |
| `image_generating` | 543, 1043 | `status` | ✔ | ✘ |
| `video_generating` | 548, 1046 | `status` | ✔ | ✘ |
| `search_status` | 555, 1049 | `status` | ✔ | ✘ |
| `compaction_status` | 561, 680 | `status` | ✔ | ✘ — desktop ใช้ `compaction_notice` แทน |
| `code_execution` | 574, 1071 | `CodeExecutionUpdate` | ✔ | ✘ |
| `tool_activity` | 580, 1076 | `ToolActivityUpdate` | ✔ | ✔ (1465) |
| `error` | 513, 589, 689, 971, 1004 | `code`, `message`, `details?`, `attempts?` | ✔ | ✔ (876/897/1027/1046/1082/1171) |
| `fallback_info` | 598, 1052 | `requested_model`, `actual_model` | ✔ | ✔ (1149) |
| `model_resolved` | 605, 1055 | `model` | ✔ | ✔ (1155) |
| `reasoning_notice` | 610, 1058 | `model`, `reason` | ✔ | ✔ (1158) |
| `provider_stall` | 617, 1063 | `stalled` | ✔ | ✘ |
| `image_notice` | 622, 1066 | `failures[]` | ✔ | ✔ (914) — **payload ต่างกัน** |
| `message_files` | 771 | `message_id`, `files[]` | ✔ | ✘ |
| `message_sources` | 798 | `message_id`, `sources[]` | ✔ | ✘ |
| `message_complete` | 741, 997, 1023, 1169, 1256 | `message_id`, `token_usage?`, `model_id?` | ✔ | ✔ (998/1003/1008/1049) |
| *(heartbeat)* `: ping` | `writeSSEHeartbeat` 473 | — comment frame ไม่ใช่ event | parseSSE ทิ้ง | ✘ |

### ฝั่ง desktop — ปล่อยผ่าน `emit()` / `emitTurnEvent()`

| event | ปล่อยจาก | chat-edge ปล่อยชนิดนี้ไหม | web รับที่ |
|---|---|---|---|
| `status` | turn-coordinator 1117 | ✘ | ✔ |
| `plan_update` | turn-coordinator 1450 | ✘ | ✔ |
| `tool_output` | turn-coordinator 1419 | ✘ | ✔ |
| `compaction_notice` | turn-coordinator 935 | ✘ (ใช้ `compaction_status`) | ✔ |
| `context_budget` | context-manager 111 | ✘ | ✔ |

### ชื่อที่ **ไม่ตรงกัน** — ผลของ A1

1. **`compaction_status` (chat-edge) vs `compaction_notice` (desktop)** — คนละความหมาย
   (`status` = "กำลังย่อ", `notice` = "ย่อไปแล้วกี่ข้อความ") web รับทั้งคู่ ไม่ต้องแก้ แต่ต้องบันทึก
2. **`image_notice` payload ต่างกัน** — chat-edge ส่ง `{failures:[{filename}]}`,
   desktop ส่ง `{filenames:[...]}`; `chatStore.ts:2489` อ่าน `data.failures[].filename` เท่านั้น
   → **ข้อความแจ้ง "ไฟล์แนบอ่านไม่ได้" ของ desktop ไม่ขึ้นเลย** (พบระหว่างสำรวจ นอกขอบเขต FIX41 — บันทึกไว้)
3. **`paused`** — `chatStore.ts` มี handler สำหรับ event ชื่อ `paused` แต่ **ไม่มีฝั่งไหนปล่อยเลย**
   (`grep '"paused"'` ทั้ง repo เจอแต่ค่า outcome ภายใน desktop) → dead branch
4. **5 ชนิดที่ desktop ปล่อยแต่ chat-edge ไม่ปล่อย** และ **8 ชนิดที่ chat-edge ปล่อยแต่ desktop ไม่ปล่อย**
   → สัญญากลาง (Part B) ต้องเป็น *ซองร่วม* ไม่ใช่ *ชุด event ร่วม*

---

## A2. ผลวัด probe ก่อนแก้

### วิธีวัด — และข้อจำกัดที่ต้องรู้ก่อนอ่านตัวเลข

**probe ทั้งหมดรันจริง ไม่ได้อ่านจากโค้ด** แต่ **ไม่ได้ยิงผ่าน stack ที่ deploy อยู่**
เพราะ **key ผู้ให้บริการทุกตัวใน `.env` เป็น placeholder** (`OPENAI_API_KEY=sk-...`) —
ยิง `POST /v1/chat/completions` ผ่าน gateway ได้ `provider_error: No fallback chain registered for model '<ทุกโมเดล>'`
(กำแพงเดียวกับ FIX33 §6.2 ที่ทำให้ probe รอบนั้นวัดไม่ได้เลย)

แทนที่จะข้าม probe ทิ้ง harness สร้าง **เส้นทางเดียวกันขึ้นมาในกระบวนการเดียวกัน**:

* chi router จริง + `middleware.RequireAuth` จริง + portal JWT ที่ mint จริง
* `MessageHandler` / `MessageService` / `StreamBroker` ตัวจริง บน **postgres-webchat ตัวจริง**
* **gateway ปลอม** (`httptest`) ที่สคริปต์ frame ขาขึ้นได้ — เพื่อให้ "provider error กลางสตรีม"
  และ "สตรีมค้างตรงกลาง" เกิดตรงจุดที่ต้องการ ไม่ใช่รอให้ provider เผลอ

ทุก hop ที่เบราว์เซอร์เดินคือ HTTP จริงในนี้ สิ่งเดียวที่ถูกแทนคือ *ใครตอบ `/chat/completions`*

รัน: `FIX41_PROBE=1 FIX41_PROBE_OUT=<dir> go test ./internal/handler/ -run TestFix41Probe -count=1 -v`
ผลดิบ: `scripts/fix41-probe/results/before/*.json`

> **บทเรียนจาก probe เอง:** รอบแรก P2 รายงาน **PASS ปลอม**
> เพราะ `httptest.Server.Close()` *บล็อกจนกว่า request ที่ค้างจะจบ* — เท่ากับ
> "รอให้ turn จบก่อนแล้วค่อย resume" ไม่ใช่ "restart กลาง turn"
> แก้แล้ว (ไม่ปิด server เก่า, สร้าง service+broker ใหม่บน DB เดิม) ผลกลับเป็น FAIL ตามจริง
> เขียนไว้ในโค้ด probe ด้วย เพื่อไม่ให้ใครทำซ้ำ

### ตารางผลก่อนแก้ (web / chat-edge)

| # | สถานการณ์ | ผลจริง | verdict |
|---|---|---|---|
| **P1** | ปิดแท็บกลางสตรีม เปิดใหม่ใน 5 วิ | ตัวอักษร **ครบ ไม่ซ้ำ** (`"ONE TWO THREE FOUR FIVE"`) แต่ **ลำดับ event เพี้ยน**: live = `message_start, model_resolved, analysis_status, reasoning_delta, analysis_status, content_delta…` / resume = `message_start, analysis_status, reasoning_delta, analysis_status, model_resolved, content_delta…` และ prefix ถูกยุบเหลือ `content_delta` ก้อนเดียว (5 → 4 เฟรม) | **PARTIAL** |
| **P1b** | tool_activity / code_execution ที่เกิดช่วงหลุด ยังอยู่ในลำดับเดิมไหม | live = `content_delta, tool_activity, content_delta, code_execution, tool_activity, content_delta, code_execution, content_delta` (8 เฟรม)<br>resume = `code_execution, tool_activity, content_delta` (**3 เฟรม**) — การ์ดเครื่องมือถูกยุบเหลือชนิดละอันและถูกดันไปไว้ **ก่อน** เนื้อความทั้งหมด | **FAIL** |
| **P2** | restart chat-edge กลางสตรีม แล้ว resume | `409 {"code":"RESUME_UNAVAILABLE"}` — broker อยู่ใน memory ล้วน รีสตาร์ตแล้วหาย ตามที่คาด | **FAIL** |
| **P3** | กด cancel กลางสตรีม | `POST /cancel` #1 → `200 {"cancelled":true}`, #2 → `200 {"cancelled":false}` (**idempotent อยู่แล้ว**)<br>แต่ client ที่ **ดูอยู่แต่ไม่ได้กด** ได้เฟรมสุดท้ายเป็น **`message_complete`** — แยกไม่ออกจากการตอบจบปกติ<br>(และก่อนหน้านั้นได้ `error` "stream error: context canceled" → เห็นเป็นบับเบิลแดงของ *ระบบล่ม* ทั้งที่ผู้ใช้กดหยุดเอง) | **FAIL** |
| **P4** | provider error กลางสตรีม | ลำดับ = `message_start, content_delta, content_delta, error, message_complete` — **เฟรมสุดท้ายคือ `message_complete`** ไม่ใช่ `error` | **FAIL** |

**ทุก probe: `run_id` = ไม่มี, `seq` = ไม่มี** (`resume_has_run_id: false`, `resume_has_seq: false`)

### ตารางผลก่อนแก้ (desktop)

| # | สถานการณ์ | ผลจริง | verdict |
|---|---|---|---|
| **P5** | ปิดแอปกลางเทิร์น เปิดใหม่ เข้าบทสนทนาเดิม | `logs` เป็น `Map` ใน memory ของ main process → กระบวนการใหม่มี ledger ว่าง → `onUiReady` replay **0 event**<br>ที่หนักกว่า: `importVisible()` / `saveLocalHistory()` ถูกเรียก **หลัง** `runLoop` จบเท่านั้น → เทิร์นที่ถูกฆ่ากลางทาง **ไม่ถูกบันทึกที่ไหนเลย** ทั้งคำถามและคำตอบครึ่งเดียวหายเงียบ | **FAIL** (วัดใน Part D/F) |
| **P6** | reload หน้าเว็บกลางเทิร์น (ไม่ปิดแอป) | `onUiReady{lastSeq}` → `log.events.filter(e => e.seq > lastSeq)` → replay ครบ ทำงานอยู่แล้ว | **PASS** |

---

## A3. terminal event — ทางออกที่ไม่ปล่อยอะไรเลย (ก่อนแก้)

### chat-edge

| จุด | มี terminal ไหม |
|---|---|
| `sendStream` เส้นทางปกติ / error / cancel | ✔ `message_complete` เสมอ (แม้ error) — **แต่นั่นคือปัญหา**: `message_complete` ถูกใช้เป็นตัวปิดของทั้งสำเร็จ ล้มเหลว และถูกยกเลิก |
| `sendStream` เมื่อ `SendStream()` fail หลังเปิด SSE แล้ว (บรรทัด 686-692) | ✘ ปล่อย `error` แล้ว **`return` ทันที ไม่มี `message_complete`** |
| `ResumeStream` เมื่อ lagged (บรรทัด 999-1005) | ✘ ปล่อย `error` แล้ว `return` |
| `ResumeStream` เส้นทางอื่น | ✔ |

### desktop

| จุด | มี terminal ไหม |
|---|---|
| `outcome === "cancelled"` | ✔ แต่เป็น `message_complete` — หน้าเว็บแยกไม่ออกว่าถูกหยุด |
| `outcome === "paused"` (ชนลิมิต 100 step / 20 นาที) | ✔ `message_complete` เหมือนกัน — ผู้ใช้ไม่รู้ว่าโดนหยุดเพราะชนลิมิต นอกจากอ่านข้อความที่แทรกใน content |
| `outcome === "error"` | ✔ ผ่าน `error` ที่ปล่อยไว้ก่อนแล้ว (บรรทัด 1171) — **แต่เฉพาะ run ระดับบนสุด** |
| `runLoop` โยน exception (ไม่ใช่ yield `error`) | ✘ `runTurn` ไม่มี try/catch คลุม → promise reject → **ไม่มี terminal event เลย** หน้าเว็บค้าง |
| `buildMessages` fail (บรรทัด 895-898) | ✔ `error` แล้ว return |

---

## A4. D3 — บั๊กที่สเปกคาดว่ามี: **ไม่มี** (พร้อมหลักฐาน)

สเปกคาดว่า: `seq` รีเซ็ตกลับ 1 ตอนเริ่ม turn ใหม่ แต่ `lastSeq` ฝั่งหน้าเว็บยังค้างที่ 40
→ frame ของ turn ใหม่ถูกทิ้งทั้งหมด

ของจริงมีการ์ด **สองชั้น** ที่กันไว้อยู่แล้ว:

1. `turn-coordinator.ts:859` และ `:1022`
   ```ts
   this.logs.set(conversationId, { seq: this.logs.get(conversationId)?.seq ?? 0, events: [] });
   ```
   **`seq` ถูกยกยอดมา ไม่รีเซ็ต** — รีเซ็ตเฉพาะ `events` (บัฟเฟอร์ replay ของ turn นี้)
2. `apps/web-chat/src/lib/bridge.ts:276`
   ```ts
   this.lastSeq.delete(conversationId);   // ใน submitTurn()
   ```
   **cursor ฝั่งหน้าเว็บถูกล้างตอนส่ง turn ใหม่**

ต่อให้ชั้นใดชั้นหนึ่งพัง อีกชั้นยังกันได้ → รอบนี้ **ไม่ได้แก้บั๊ก** แต่ **เขียน test ที่ยิงสอง turn ติดกัน**
ตามที่สเปกสั่ง เพื่อให้การ์ดสองชั้นนี้กลายเป็นสัญญาที่ถูกพิสูจน์ ไม่ใช่ความบังเอิญ (Part D)

**ความเสี่ยงที่ยังเหลือ และเป็นเหตุผลจริงที่ต้องมี `run_id`:** ทั้งสองการ์ดตั้งอยู่บนสมมติฐาน
"seq เดินหน้าอย่างเดียวตลอดอายุกระบวนการ" — พอ Part F เขียน ledger ลงดิสก์แล้วอ่านกลับมาหลังรีสตาร์ต
สมมติฐานนี้จะไม่จริงอีกต่อไป `run_id` คือสิ่งที่ทำให้ cursor ถูกต้องโดยไม่ต้องพึ่งสมมติฐานนั้น

---

## A5. ledger — ที่เก็บปัจจุบัน

| ด้าน | ปัจจุบัน |
|---|---|
| chat-edge | `StreamBroker` — `map[uuid.UUID]*brokerEntry` ใน memory, **ไม่มี Redis** (แม้ `redis-webchat` จะรันอยู่และ go-redis เป็น dep แล้ว) |
| เก็บอะไร | สถานะ *ล่าสุด* ของแต่ละชนิด + `strings.Builder` ของเนื้อความ — **ไม่ใช่รายการ event** |
| TTL | `resumeRetainTTL = 60s` หลัง terminal (hard-coded) |
| cap | `resumeSubBuffer = 1024` ต่อ subscriber (hard-coded) |
| desktop | `logs: Map<string, {seq, events[]}>` ใน memory ของ main process — ไม่มี TTL ไม่มี cap ไม่ลงดิสก์ |

**เหตุผลเชิงโครงสร้างที่ P1b พัง:** `snapshotLocked()` เก็บ `codeRuns map[string]` + `codeOrder []string`
คือ *เฟรมล่าสุดต่อ id ตามลำดับที่เห็นครั้งแรก* — ตำแหน่งสัมพัทธ์กับ `content_delta` **ไม่ได้ถูกเก็บเลย**
ไม่ว่าจะเขียน replay ดีแค่ไหน ข้อมูลนั้นไม่มีอยู่ใน snapshot ตั้งแต่ต้น

---

## A6. baseline ของชุดทดสอบ (ก่อนแก้ บนเครื่องนี้)

| ชุด | ผล | คำสั่ง |
|---|---|---|
| chat-edge Go | **ทุก package `ok`** (config, handler, middleware, repository, router, sandbox, service, storage) | `go test ./... -count=1` |
| desktop vitest | **345 passed / 0 failed** (36 ไฟล์) | `npx vitest run` |
| web-chat vitest | **555 passed / 0 failed** (79 ไฟล์) | `npx vitest run` |
| backend pytest | รันแยก (ดู FIX41_REPORT §เกณฑ์ 10) | in-container |
| `cargo test` | **รันไม่ได้บนเครื่องนี้** — ไม่มี Rust toolchain (`~/.cargo` ไม่มี, `rustc` ไม่อยู่ใน PATH) และ image `dlp-sidecar` เป็น distroless (ไม่มีแม้ `sh`) | — |

`cargo test` รอบนี้ **แทนด้วยหลักฐานที่แข็งกว่า**: FIX41 ไม่แตะไฟล์ Rust แม้แต่ไฟล์เดียว
พิสูจน์ด้วย `git diff --stat` ว่า `dlp-sidecar/` ว่างเปล่า

---

## A7. สรุปสิ่งที่ต้องทำจริง (หลังตัดของที่มีแล้วออก)

| ต้องทำ | ฝั่ง | เพราะ probe ไหน |
|---|---|---|
| `run_id` + `seq` ในทุกซอง | ทั้งสอง | ทุก probe — ไม่มีเลย |
| event `cancelled` | ทั้งสอง | P3 |
| terminal ที่บอกได้ว่าจบยังไง (`error` ต้องเป็นเฟรมสุดท้ายจริง) | ทั้งสอง | P3, P4 |
| terminal ครบทุกทางออก (รวม path ที่ `return` เงียบ) | ทั้งสอง | A3 |
| ledger เป็น **รายการ event** ไม่ใช่ snapshot ที่พับแล้ว | chat-edge | P1, P1b |
| ledger ทน restart | chat-edge | P2 |
| ledger ทนปิดแอป | desktop | P5 |
| ไม่ทำให้ P6 พัง | desktop | P6 |
