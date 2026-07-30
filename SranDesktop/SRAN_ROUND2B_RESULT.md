# SRAN Desktop — Round 2b Result (retrying state ✅ · goal→steps grouping ⏸️ report)

**สถานะ:** ✅ งาน 1 (retrying state) + งาน 3 (polish/tokens/i18n) เสร็จ · ⏸️ งาน 2 (grouping) หยุดรายงาน (โครงข้อมูลจริงต่างจากคาด — ต้องตัดสิน UX ก่อน)
**Test:** main **1292** (+9) · UiTests **5** (+1 retrying) · 0 fail · STA guard เขียว + mutation ยืนยันแล้ว
**ไม่แตะ backend/runtime** (2a จบแล้ว) — presentation + event consumption เท่านั้น

---

## ✅ งาน 1 — retrying state (กลืน X แดงซ้ำ)

**consume `AgentToolRetryEvent` (จาก 2a) → ยุบเป็นการ์ดเดียว**

| ไฟล์ | แก้ |
|---|---|
| `ViewModels/AgentExecutionViewModel.cs` | + `IsRetrying`, `RetryHint`, `BeginRetry(attempt,max,reason)`, `RetryReason()`; `_state` เพิ่ม `NotifyPropertyChangedFor(IsRetrying)`; `Complete()` เคลียร์ `RetryHint` |
| `ViewModels/ChatViewModel.cs` | + case `AgentToolRetryEvent` → หา card ตาม `ToolCallId` → `BeginRetry(...)` (ไม่สร้างการ์ดใหม่) |
| `Controls/MessageBubble.xaml` | + amber pulse dot (IsRetrying) ใน status grid + `{Binding RetryHint}` amber ใน header |
| `Localization/Dict.cs` | + `tool.retrying` + `tool.retry.reason.*` (5) ครบ **TH+EN** |

**พฤติกรรม:** ระหว่าง auto-retry การ์ดเป็น **amber pulse + "กำลังลองใหม่ n/m — โฟกัสหลุด"** (ไม่มี X แดงระหว่างทาง เพราะ `IsFailed=false` ตอน state=`retrying`) → จบ retry: สำเร็จ → verified/verification_pending ตามจริง; ล้มถาวร/เกิน budget → `Complete()` เคลียร์ hint + failed + **human-error (R5) ยังทำงาน**

**Producer:** publisher คือ R4 orchestrator loop ที่ **staged ใน 2a** — เมื่อลงมือ round 2a-2 การ์ดจะเปลี่ยน amber อัตโนมัติผ่าน event นี้ (UI พร้อม consume แล้ว)

## ✅ งาน 3 — polish
- ทุกสีผ่าน `DynamicResource` (`AccentAmberBrush` จาก R5, ไม่ hardcode); reuse status-dot/pulse pattern เดิม ไม่ทำ visual ใหม่เกินจำเป็น
- localization ครบ TH+EN

## ✅ STA guard (บังคับ) — อัปเดตแล้ว
- เพิ่มเคส `Retrying_Card_Shows_Amber_Hint_Not_Fail_Glyph` ใน `SranDesktop.UiTests`: assert TextBlock ผูก `RetryHint` **visible** + **ไม่มี fail glyph** (Geo_Close)
- **mutation ยืนยัน:** เปลี่ยน `Text="{Binding RetryHint}"` → binding ผิดชั่วคราว → STA test **แดง** (Failed: 1) → revert → เขียว ✅
- `x:Name="ToolExecutionsList"` เดิมไม่เปลี่ยน (งาน 1 ไม่ restructure โครง) → STA test เดิม 4 เคสยังครอบ HumanError/amber/fail-glyph/expanded ตามเดิม

**VM unit tests (deterministic, 9 ใหม่):** `Round2bRetryStateTests.cs` — BeginRetry→retrying+hint, Complete เคลียร์ hint + R5 คงอยู่, retry→verified ไม่มี hint, RetryReason map

---

## ⏸️ งาน 2 — goal→steps grouping: หยุดรายงาน (ตามกติกา)

**ที่สั่ง:** nest การ์ด tool ใต้แต่ละ step (checklist). พอเจาะโค้ดจริง โครงข้อมูล **ต่างจากที่คาด** ใน 3 จุดที่ทำให้ grouping สะอาดตัดสินไม่ได้เองโดยไม่เดา:

1. **checklist ปัจจุบัน = Outcomes ไม่ใช่ Steps:** `MessageViewModel.UpdatePlan(AuthoritativePlanView)` สร้าง `PlanSteps` จาก **`plan.Outcomes`** (`outcome.Description/Status`, MessageViewModel.cs:44-49) — `PlanStepViewModel` มีแค่ (text, status) **ไม่มี StepId**. ส่วน `AuthoritativePlanView.Steps` (มี `StepId`) **ถูกมองข้าม**
2. **tool ผูก StepId ได้เฉพาะตอน result + durable เท่านั้น:** `AgentToolResultEvent.UnifiedResult.StepId` (มีจริง) โผล่ **หลัง** action เสร็จ, และ **null ในโหมดแชตธรรมดา** (durable ไม่ทำงาน). ตอน `AgentToolCallsEvent` (สร้างการ์ด) **ยังไม่มี StepId** → การ์ดจะเกิดแบบ ungrouped แล้วค่อย "กระโดด" เข้ากลุ่มตอนเสร็จ (jumpy)
3. **granularity ไม่ตรง:** join key ที่ตรงคือ StepId แต่ checklist โชว์ outcomes (คนละ entity) → ต้องสลับ semantics ของ checklist จาก outcomes→steps ก่อน ซึ่งเปลี่ยน "สิ่งที่ผู้ใช้เห็น" (อาจ regress UX)

**ทำไมไม่เดาแก้:** grouping ที่ถูกต้องต้องตัดสิน UX 3 อย่าง — (ก) checklist เป็น outcomes หรือ steps, (ข) การ์ดที่ StepId มาช้า/ไม่มี (non-durable) จัดกลุ่มยังไง (กระโดด vs bucket "อื่น ๆ"), (ค) fallback โหมดแชตธรรมดา = flat เหมือนเดิม — เป็นการตัดสินใจดีไซน์ที่ไม่ควรเลือกแทนโดยพลการ (กติกา: "อย่าประดิษฐ์ planner ใหม่" + "ต่างจากคาด → หยุดรายงาน")

**ข้อมูล step มีจริง** (ไม่เข้าเงื่อนไข "ไม่มีข้อมูล step เลย") — แค่ต้อง enrich VM + เลือก UX ก่อน

### design ที่พร้อมลงมือ (ถ้าอนุมัติ + เคาะ UX)
- enrich `PlanStepViewModel` ให้พก `StepId` (จาก `AuthoritativePlanView.Steps`); เก็บ `PlanStepsById`
- `ToolExecutionItemViewModel` พก `StepId` (set ตอน `Complete` จาก `UnifiedResult.StepId`) + `StepHeader`
- group `ToolExecutionsList` ด้วย `CollectionViewSource` grouping บน StepId → `GroupStyle` header = step description + progress n/m
- fallback: StepId null → กลุ่ม "ungrouped/ทั่วไป" (โหมดแชต = 1 กลุ่ม = flat เดิม, ไม่ regress)
- STA guard: เพิ่มเคส grouped locate การ์ด/HumanError/amber ใต้ group header + mutation

> **หมายเหตุ:** งาน 1 (retrying) เป็น "ตัวกลืน X แดง" หลักของรอบ ทำเสร็จแล้ว; grouping เป็น layout/hierarchy ที่แยกได้และควรเคาะ UX ก่อน

---

## ยังไม่ได้ยืนยันด้วยตาจริง (รอ batch live validation)
- **สี/animation:** amber pulse dot + RetryHint แสดงถูกสีจริงบน theme สว่าง/มืด, จังหวะ pulse — STA เช็ค element/visibility/text แล้ว แต่ **ไม่เช็ค pixel/animation** (ตามกติกา)
- **theme-switch runtime:** ยังไม่ยืนยันตอนสลับ light/dark ระหว่างการ์ด retrying
- **flow จริง:** retrying state ยังไม่เห็นบนแอปจริง เพราะ **producer (R4 loop) ยัง staged** — จะเห็นเมื่อทำ round 2a-2

---

## Build / Test
```
dotnet build SranDesktop.sln  → Build succeeded
dotnet test  SranDesktop.sln  → main 1292 / UiTests 5 / 0 fail
```
คง R3′ (amber), R5 (human-error), progressive disclosure ครบ (ไม่ regress)

## เลือกทางต่อ
- **(ก)** เคาะ UX 3 ข้อของ grouping (checklist outcomes/steps · late-StepId · non-durable fallback) แล้วผมลงมืองาน 2 ตาม design
- **(ข)** ทำ **round 2a-2** (R4 loop) ก่อน เพื่อให้ retrying state มี producer จริง แล้วค่อย grouping
- **(ค)** validate live batch (retrying + amber + human-error) ก่อนไปต่อ

**หยุดรอรีวิว**
