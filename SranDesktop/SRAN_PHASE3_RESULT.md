# SRAN Desktop — Phase 3 (router substitute) — RE-ASSESS

> อ้างอิงแผน: `SRAN_UIA_FIRST_ARCH_PLAN.md` Phase 3 (Item 2 router substitute) + prompt STEP 0 re-assess
> สถานะ: ✅ **ปิด Phase 3 — Beat เลือกทาง C (2026-07-25): ยอมรับ redirect, ไม่แก้โค้ด, ไป Phase 4**
>
> **การตัดสินใจ:** Beat เลือก *"C: ยอมรับ redirect + ข้ามไป Phase 4 (แนะนำ)"* → **ไม่แตะ router/selection**. เหตุผล: (1) bounce = redirect ไป `uia_click` = gold path ที่ verify ได้ (ไม่ใช่การ์ดแดง), (2) Phase 1 คุม plain-chat แล้ว, (3) ทุกทางที่ตัด bounce จริง (A/D) แตะ verification-first design หรือใหญ่เกินก้อนเล็ก. **loop-in-theory ยอมรับได้** เพราะโมเดลจริง converge ไป uia_click. Phase 3 = moot → งาน bounce ปิด, ไปต่อ Phase 4 (retry-engine) เมื่ออนุมัติ.
>
> **ไม่มีการแก้โค้ด/test ในเฟสนี้** — repo คงสถานะจาก Phase 2 (main 1334 + UiTests 5). ไม่แตะ Phase 4.

---

## ส่วนแรก — ผล RE-ASSESS (จากโค้ดจริง, `path:line`)

### Q1: bounce ยังเกิดจริงไหมหลัง Phase 1? → **ใช่ ยังเกิด (agent-mode)**

trace `CapabilityRouter.Route` สำหรับ `computer_click` + outcome `OperationProcessed`:
- candidateIds = `availableCapabilities` (relevant + modality-matched) + requestedTool (`DurableTaskRuntime.cs:2262-2271`)
- `IsToolRelevant(OperationProcessed)` = `uia_click`/`computer_click`/`computer_key`/`browser_click` → ทั้ง `uia_click` และ `computer_click` เข้า candidate (`RuntimeActionPolicy.cs:74-78`)
- `uia_click` **อยู่ใน capability inventory** จริง (`AppHostBuilder.cs:322-329`, บรรทัด 328)
- `BaseScore`: `uia_click=90` > `computer_click=55` (`DurableTaskRuntime.cs:2353-2358`)
- selected = `uia_click` ≠ requested `computer_click` → **`CapabilitySelectionRequired` reject** (`DurableTaskRuntime.cs:2300-2310`)
- consume: PrepareAction `route.Allowed==false` → `Block(...)` (`:536-550`) → ChatOrchestrator ปฏิเสธ + ส่ง error กลับโมเดล (`ChatOrchestrator.cs:557-569`)

**⇒ แม้ Phase 1 ทำ computer_click เป็น UIA-first ในตัวแล้ว แต่ใน agent-mode `computer_click` ถูก router ปฏิเสธ *ก่อน* ได้รัน → UIA-point-first ของ Phase 1 ไม่ถูกใช้ใน agent-mode เลย** (ใช้แค่ plain-chat ที่ไม่มี router)

### Q1b: bounce ถูก bound ไม่ให้ loop ไหม? → **ไม่ (unbounded ในทางทฤษฎี)**

- `TaskAction` + `step.AttemptCount++` ถูกสร้าง **เฉพาะเมื่อ route ผ่าน** (`DurableTaskRuntime.cs:634-662`) ซึ่งอยู่ **หลัง** จุด reject (`:548`)
- routing reject จึง **ไม่สร้าง action, ไม่เพิ่ม AttemptCount**
- `IsCapabilityExhaustedForStep` นับเฉพาะ `stepActions` ที่ `Status==Failed` (`DurableTaskRuntime.cs:2119-2122`) → reject ไม่ถูกนับ
- `RetryBudget` (`:380`) / `ActionBudget` (`:382`) ก็อาศัย AttemptCount → ไม่ถูกกระตุ้น

**⇒ ถ้าโมเดลดื้อเรียก `computer_click` ซ้ำ ๆ จะโดน reject ซ้ำได้ไม่จำกัด** — ไม่มี counter หยุด. (ในทางปฏิบัติโมเดลที่ดีจะอ่าน error แล้วสลับไป read_screen_elements→uia_click ซึ่ง redirect ไป **gold path ที่ verify ได้** จึง converge; แต่ loop-in-theory มีจริง)

### Q1c: bounce นี้ "แย่" แค่ไหน?
redirect ไปลงที่ `uia_click` = **structured/verifiable path ที่ดีที่สุด** (bind explicit element จาก snapshot). ไม่ใช่การ์ดแดง, ไม่ใช่ coordinate. → เป็น "redirect-to-gold-path" ที่ converge กับโมเดลจริง; ข้อเสียจริงคือ (ก) 1-2 round-trip เกิน (read_screen_elements ก่อน) และ (ข) loop-in-theory เมื่อโมเดลดื้อ

### Q2: safety-test ที่ห้ามพัง
`RuntimeStabilizationTests.cs:479-497` `Runtime_Selects_Uia_Value_Before_Generic_Type`: assert `Route()` (unit) คืน `CapabilitySelectionRequired`, `SelectedCapability="uia_set_value"` สำหรับ **`computer_type` + TextValue**. → เป็นเรื่อง **type** เท่านั้น. `CapabilitySelectionRequired` ถูก assert ที่เดียวนี้ (grep). `computer_click`-bounce **ไม่มี test lock ตรง ๆ** แต่ RS-001 คาด `OperationCapability=="uia_click"` (`RS001RegressionTests.cs:94`) → การเปลี่ยน selection ของ click อาจกระทบ RS-001 ทางอ้อม

---

## ส่วนสอง — ทางเลือก + tradeoff (ทำไมต้องถามก่อน)

| ทาง | ทำอะไร | ตัด bounce? | ชน safety? | feasible? |
|---|---|---|---|---|
| **A** หยุด router reject `computer_click` | ให้ computer_click (UIA-first แล้ว) proceed แทน bounce ไป uia_click (เฉพาะ OperationProcessed, **ไม่แตะ type**) | ✅ ตัดขาด | ไม่ชน test ที่ชื่อระบุ (เป็น type) **แต่กลับด้าน design verification-first** (ตั้งใจ prefer uia > computer) และอาจกระทบ RS-001 `OperationCapability=="uia_click"` ทางอ้อม | ✅ |
| **B** substitute จริง (computer→uia) | รัน uia_click แทนให้ | ✅ | — | ❌ **infeasible** (uia_click ต้อง element_id/snapshot_id ที่โมเดลไม่ส่ง; ไม่มี re-dispatch machinery — ยืนยันตั้งแต่ Phase 1) |
| **C** ยอมรับ redirect-to-gold-path | ไม่แก้ selection; ถือว่า bounce = redirect ไป uia_click (path ดีสุด) | ❌ (คง redirect) แต่ Phase 1 คุม plain-chat แล้ว | ✅ ไม่ชน | ✅ (ไม่ต้องแก้โค้ด — แค่ปิด Phase 3 เป็น moot + ไป Phase 4) |
| **D** loop-guard (คง selection เดิม) | คง router prefer uia (test เขียว) **แต่ทำให้ redirect ซ้ำ ๆ มี bound** (นับ redirect ต่อ step → escalate เป็น pause/switch แทน loop) | ✅ ตัด *loop* (ไม่ใช่ redirect แรก) | ✅ ไม่ชน (Route() เดิม, test เขียว) | ⚠️ ต้องเพิ่ม **redirect-counting persistence** (event/counter ต่อ step) — ใหญ่กว่า "ก้อนเล็ก" |

### ข้อสังเกตสำคัญ
- **A** เป็นวิธีเดียวที่ "ตัด bounce ให้ขาด" ตรง ๆ แต่มัน **กลับด้านการตัดสินใจ verification-first ที่ตั้งใจ** (router จงใจ prefer uia > computer). แม้ test ที่ชื่อระบุจะไม่แดง แต่มันคือการเปลี่ยน design intent — เป็นการตัดสินใจของ Beat ไม่ใช่ของผม
- **D** ปลอดภัยเชิง semantic ที่สุด (ไม่แตะ selection) แต่ต้องมี machinery ใหม่ (นับ redirect) — เกินกรอบ "Phase 3 ก้อนเล็ก" และแตะ persistence
- **C** ซื่อสัตย์ที่สุดถ้ามองว่า redirect-to-uia_click คือพฤติกรรมที่ *ถูกต้องอยู่แล้ว* (Phase 1 ปิด plain-chat, agent-mode ได้ gold path) — Phase 3 เกือบ moot

**⇒ ทุกทางที่ "ตัด bounce จริง" (A/D) ล้วนแตะ design/safety หรือใหญ่เกินก้อนเล็ก. ตาม prompt STEP 0 ("ถ้าไม่ชัดหรือทุกทางเสี่ยง safety → หยุดถามก่อนลงมือ") → หยุดถาม**

---

## ส่วนที่เหลือ (ค/ง/จ) — รอผลการตัดสินใจ
diff / VM-tests / live-validate จะเติมหลัง Beat เลือกทาง · **ยังไม่แตะโค้ด, ไม่แตะ Phase 4**

> **หยุดที่นี่ — รอ Beat เลือกทาง (A / C / D) ก่อนลงมือ**
