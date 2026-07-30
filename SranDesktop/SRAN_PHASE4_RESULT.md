# SRAN Desktop — Phase 4 (wire retry-engine) — STEP 0 RE-ASSESS

> อ้างอิง: `SRAN_ROUND2A_RESULT.md` R4 design + `SRAN_UIA_FIRST_ARCH_PLAN.md` Phase 4 + prompt STEP 0
> สถานะ: ✅ **ปิด Phase 4 no-code — Beat เลือก (2026-07-25) หลังเห็นผล STEP 0/0b**
>
> **การตัดสินใจ:** *"ปิด Phase 4 no-code (แนะนำ)"* → **ไม่แก้โค้ด**. retry คงเป็น LLM-driven (ทำงานอยู่, การ์ดแยกตามจริง). เหตุผลรวบยอด: (1) auto-rebind ของ path หลัก `uia_*` **infeasible** (element_id/snapshot_id regenerate, runtime ไม่ถือ descriptor), (2) collapse UX ผูกกับ in-place producer (Option A) ที่เลี่ยงไปแล้ว, (3) transient หลัก (FocusLost) แก้ในตัว tool แล้ว (R2 ladder + Phase 1 UIA-point-first). `TransientFailurePolicy` + `AgentToolRetryEvent` + 2b UI คงเป็น **scaffolding** พร้อมใช้ถ้าอนาคตทำ Option A. **ไม่มีการแก้โค้ด/test** — repo คง Phase 3 (main 1334 + UiTests 5).

---

## STEP 0 — design เดิม (2a) valid กับโค้ดปัจจุบันไหม?

### ✅ ส่วนที่ยัง valid: guard interaction
Design 2a (`SRAN_ROUND2A_RESULT.md:56-60`) จะ re-observe → rebind → re-run ภายใน budget. ตรวจ guard ปัจจุบันแล้ว **สอดคล้องกัน**:
- **FreshObservationRequired** (`DurableTaskRuntime.cs:571-576`): step ที่ Failed + code ต้อง fresh-observe + ไม่ใช่ observation tool → Block → ต้อง re-observe ก่อน re-run (ตรงกับ design)
- **BlindRetryBlocked** (`:582-612`): retry ผ่านได้เมื่อ **fresh observation** (WorldState.Version bump + source เป็น observation) **หรือ args เปลี่ยน** (fingerprint) — การ rebind capture_id ทำให้ทั้งสองจริง → ผ่าน guard
- **RetryBudget** (`:380`) / **DuplicateActionPrevented** (`:616-627`, idempotencyKey ผูก fingerprint `:614`): re-run ที่ args เปลี่ยนจะได้ key ใหม่ ไม่โดน duplicate; และนับ AttemptCount → เกิน budget = `RetryBudgetExceeded`
- `RequiresFreshObservation` (`:1652-1658`) vs `TransientFailurePolicy.IsRetryable`: ทุก retryable code ต้อง re-observe เพื่อผ่าน BlindRetry → design ถูกต้อง

**⇒ กลไก guard ของ design ยังใช้ได้ — ไม่ใช่จุดที่หยุด**

### ❌ ส่วนที่ **ไม่** valid แล้ว: rebind mechanism ไม่ครอบ path ที่ Phase 0-3 steer ไปหา

Design 2a สมมติ **"parse capture_id ใหม่ → เขียนทับ args"** (`SRAN_ROUND2A_RESULT.md:45`). แต่ (ยืนยันจากโค้ด):

1. **`uia_*` rebind ไม่ได้เลย** — `uia_click`/`uia_set_value` ใช้ `element_id`+`snapshot_id` จาก `read_screen_elements` (ไม่ใช่ `capture_id`). fresh observation = `read_screen_elements` ใหม่ → **snapshot_id ใหม่ + element_id re-index** (เลขเดิมชี้คนละ element). **runtime ไม่ถือ semantic descriptor ของ element เลย** (`grep -c "element_id|snapshot_id"` ใน `DurableTaskRuntime.cs` = **0**) → runtime **สังเคราะห์ args ใหม่ให้ uia_* ไม่ได้** — retry ต้องให้ **โมเดล** re-pick element เท่านั้น

2. **`computer_click` bare-x/y rebind ไม่ปลอดภัย** — rebind `capture_id` ได้ แต่ **x/y ผูกกับ screenshot เดิม**; fresh screenshot อาจทำให้พิกัดชี้ผิดที่ → auto-rebind แค่ capture_id = คลิกมั่ว (runtime ไม่รู้ target เพื่อ re-derive x/y)

3. **เหลือ `computer_type` ตัวเดียวที่ rebind สะอาด** — semantic (`target_description`) + `capture_id`: re-observe → เปลี่ยน capture_id → tool re-`FindTarget` control จาก description บน window ใหม่ (re-resolve ตัวเอง). ปลอดภัย **แต่** transient ที่พบบ่อยสุดของมัน (**FocusLost**) ถูก **R2 ladder + focus-retry 3x แก้ในตัว tool แล้ว** (`SRAN_ROUND2A_RESULT.md:19,54`) + Phase 1 UIA-point-first (ไม่ต้อง foreground) → residual value เล็ก

### landscape เปลี่ยนเพราะ Phase 0-3 (นี่คือหัวใจ)
Phase 0 (register order + description) และ Phase 3 (agent-mode router redirect `computer_click`→`uia_click`) **steer traffic ไปหา `uia_*`** — ซึ่งคือ path ที่ auto-rebind **ทำไม่ได้**. ส่วน `computer_*` (rebindable) กลายเป็น fallback ส่วนน้อย และตัวที่ retry สะอาดจริง (`computer_type`) ก็แก้ transient หลักในตัวแล้ว

**⇒ การผ่าตัด `ExecuteToolCallAsync` (invariant-critical) เพื่อ auto-rebind จะได้ประโยชน์แค่ slice แคบ (`computer_type` residual) ขณะที่ path หลัก (`uia_*`) ยังต้องพึ่ง LLM-retry อยู่ดี — cost/risk ไม่คุ้มแบบ design เดิม**

---

## ทางเลือก (ทุกทางเคารพ invariant; ต่างที่ scope/คุณค่า)

| ทาง | ทำอะไร | ครอบ path หลัก (uia_*)? | risk ต่อ invariant | คุณค่า |
|---|---|---|---|---|
| **A** narrow auto-retry: **เฉพาะ `computer_type`** (semantic+capture_id) | re-observe→rebind capture_id→re-run ภายใน budget; ยุบเป็น "retrying n/m"; tool อื่นคง LLM-retry เดิม | ❌ | กลาง (ยังผ่าตัด `ExecuteToolCallAsync` แต่ scope แคบ, guard ครบ) | เล็ก (FocusLost แก้ในตัวแล้ว; เหลือ TargetNotFound/late-control) |
| **B** generic auto-retry (design 2a เต็ม) | rebind ทุก tool | — | **สูง/ไม่ feasible** (uia_* rebind ไม่ได้, computer_click x/y มั่ว) | — ❌ ตัดทิ้ง |
| **C** defer การผ่าตัด: wire policy แบบเบา | ไม่แตะ re-run loop; ใช้ `TransientFailurePolicy` classify (มีแล้ว) + (ออปชัน) emit `AgentToolRetryEvent` เมื่อ **โมเดล** re-call retryable-fail step เดิม → 2b UI ยุบการ์ด โดยไม่ arg-surgery | ✅ (ทำงานกับทุก tool เพราะ retry ยังเป็น LLM-driven) | ต่ำ (ไม่แตะ durable re-run/idempotency) | กลาง (ได้ collapse UI + ไม่เสี่ยง invariant; ไม่ได้ "invisible auto-retry" เต็ม) |

### ข้อสังเกต
- **A** = design เดิมแต่หดเหลือ tool เดียวที่ปลอดภัย — ผ่าตัดจุดเปราะเพื่อ slice แคบ
- **C** = ยอมรับว่า retry ที่ครอบ path หลัก (uia_*) **ต้อง** เป็น LLM-driven (runtime rebind ไม่ได้), แล้วโฟกัสที่ **collapse UX** (ยุบการ์ดแดงซ้อน→"retrying n/m") ผ่าน event producer เบา ๆ ที่ correlate การ re-call — ปลอดภัยต่อ invariant ที่สุด และครอบทุก tool
- **B** ตัดทิ้ง (พิสูจน์แล้วว่า uia_* rebind infeasible)

**ตาม STEP 0 ("โครงต่างจาก design → หยุด") + กติกา ("ถ้าต้องเดาเพื่อผ่าน ให้หยุดถาม ดีกว่า ship durable guarantees พัง") → หยุดถามก่อนลงมือ**

---

---

## STEP 0b — ตรวจ option C (ที่ Beat เลือก) กับ UI จริง → พบว่า collapse ผูกกับ in-place retry

Beat เลือก **C (light wiring, ไม่ผ่าตัด re-run)**. เจาะ 2b UI consumer ต่อ แล้วพบ **ข้อขัดโครงสำคัญ**:

- `AgentToolRetryEvent` ถูก map เข้าการ์ด **ด้วย `ToolCallId`** (`ChatViewModel.cs:1531-1536`: `FirstOrDefault(t => t.ToolCallId == retry.ToolCallId)?.BeginRetry(...)`)
- 2b UI ถูกออกแบบสำหรับ **R4 in-place loop ที่ retry ด้วย `toolCall.Id` เดิม** (`SRAN_ROUND2B_RESULT.md:22`, `SRAN_ROUND2A_RESULT.md:60`) → การ์ดเดิมเปลี่ยนเป็น amber
- **แต่ C = ให้โมเดล re-call เอง → `toolCall.Id` ใหม่ → การ์ดใหม่** (สร้างที่ `ChatViewModel.cs:1518` ตอน `AgentToolCallsEvent`) → retry event ที่ยิง id เดิมจะไม่ยุบการ์ดใหม่; จะได้ **สองการ์ด** ไม่ใช่การยุบ

**⇒ collapse UX ("retrying n/m" การ์ดเดียว) ผูกกับ producer ที่ retry in-place ด้วย id เดิม — ซึ่งคือ Option A (narrow in-place retry).** "light wiring ไม่ผ่าตัด re-run" (C) **ไม่มี in-place retry ให้ยุบ** → ขับ collapse UI ไม่ได้

การจะยุบ re-call แยก ๆ (id ต่างกัน) ให้เป็นการ์ดเดียว = ต้องมี **cross-card correlation ตาม step** — ซึ่งคือ **2b งาน 2 (grouping) ที่ staged ไว้เพราะต้องเคาะ UX 3 ข้อ** (`SRAN_ROUND2B_RESULT.md:37-47`): checklist outcomes/steps · StepId มาช้า/null ในแชตธรรมดา · fallback flat. **ผมจะไม่ประดิษฐ์ correlation/UX เอง** (กติกา: "โครงต่างจาก design → หยุด", "อย่าเดาผ่าตัด")

### สรุป entanglement (หัวใจของ STEP 0b)
| อยากได้ | ต้องมี | สถานะ |
|---|---|---|
| collapse "retrying n/m" การ์ดเดียว | in-place retry (same toolCall.Id) = **Option A** | A = narrow surgery (computer_type), Beat เพิ่งเลี่ยง |
| ยุบ re-call แยก ๆ (LLM-driven) | step-grouping correlation = **2b งาน 2** | staged, ต้องเคาะ UX 3 ข้อ |
| ไม่ทำทั้งสอง | — | retry คง LLM-driven, การ์ดแยกตามเดิม (no collapse) |

**⇒ C แบบที่จินตนาการ (light + collapse) เป็นไปไม่ได้แบบสะอาด — collapse ต้องพึ่ง A หรือ grouping. ต้องให้ Beat เลือกใหม่บนข้อเท็จจริงนี้**

---

## Phase 4 = ปิด no-code · ไม่แตะ `ExecuteToolCallAsync` หรือโค้ดใด ๆ · repo คง Phase 3 (main 1334 + UiTests 5)

---

## 🔎 BATCH LIVE-VALIDATE — ครบ Phase 0–4 (ผู้ใช้ทำเอง)
> ปิด `SranDesktop.exe` ให้สุด → build (full solution ได้เมื่อแอปปิด) → เปิดใหม่ก่อน · ทดสอบโดย **ไม่ให้ computer-use แย่ง foreground**

**Phase 0 — mapping fix + steering**
- [ ] ทำ action ที่ **ไม่ใช่ click** ให้ล้มด้วย policy/consent → การ์ด error **ไม่** ขึ้น "Windows didn't accept the click" (พิสูจน์ `Has("rejected")` fix)
- [ ] `OsInputRejected` จริง → ยังขึ้นข้อความ osRejected ตามเดิม
- [ ] สั่งงานเดสก์ท็อป → โมเดลเรียก `read_screen_elements`/`uia_*` มากขึ้น (structured ถูก list ก่อน)

**Phase 1 — UIA-point-first**
- [ ] "เปิด Notepad คลิกปุ่ม/เมนู" (agent-mode) → ใช้ UIA path, **ไม่มีการ์ด coordinate แดง**, SRAN ไม่แย่ง foreground
- [ ] **plain-chat** สั่งคลิก control → ได้ UIA-point-first เหมือนกัน (พิสูจน์ tool-level ครอบ plain-chat)
- [ ] คลิก canvas/พื้นที่วาด (ไม่มี UIA element) → ตก coordinate ได้ตามเดิม (fallback)

**Phase 2 — evidence polish**
- [ ] พิมพ์ใน Notepad → การ์ดโชว์ `no_foreground_rung=true` + `injection_method=` เดิมยังอยู่; สถานะเขียว/amber ไม่เปลี่ยน
- [ ] แอปที่ต้อง keystroke → `no_foreground_rung=false`

**Phase 3 — router redirect (agent-mode)**
- [ ] agent task ที่โมเดลลองเรียก `computer_click` → ถูก redirect ไป `uia_click` แล้ว **ลงเอยเขียว** (verify ได้), **ไม่ loop ไม่จบ** (โมเดลจริง converge)

**Phase 4 — retry คง LLM-driven**
- [ ] transient (เช่น control โผล่ช้า/TargetNotFound) → โมเดล re-observe + re-call เอง แล้ว **converge** (ไม่ค้าง, ไม่ระเบิดการ์ดแดงถาวร); ยืนยันไม่มี regression จาก Phase 0-3

> **หยุดที่นี่ — จบทั้ง 5 เฟส (0-4). รอผล batch live-validate ก่อนถือว่าปิดงานสมบูรณ์**
