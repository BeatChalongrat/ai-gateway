# SRAN Desktop — Round 2a Result (R2 ladder + retry scaffolding; R4 loop staged)

**สถานะ:** ✅ ที่ส่งมอบ build + test เขียวทั้งหมด (main **1283** = 1259 + 24 ใหม่, UiTests **4**, 0 fail)
**ส่งมอบ:** R2 (in-tool ladder) + `AgentToolRetryEvent` + `TransientFailurePolicy` (retryable set ที่เดียว) + tests
**⏸️ Staged (รอรีวิว design ก่อนลงมือ):** R4 orchestrator auto-retry loop — เหตุผลด้านล่าง (เจอความเสี่ยงต่อ durable invariants ที่ควรหยุดรายงานก่อน)

---

## ✅ R2 — in-tool typing ladder (computer_type) — เสร็จ

**ไฟล์:** `Mcp/Tools/ComputerTypeTool.cs` (แทน injection path เดี่ยวเดิม)

พอร์ต ladder จาก `UiaSetValueTool` — แต่ละ rung ผูกกับ capture HWND/PID เดิม, verify read-back ต่อ rung, หยุดเมื่อสำเร็จ:
1. **ValuePattern** (Edit/WinForms/web)
2. **window-message** `ReplaceControlText` → control HWND (ถึง Win11 RichEdit, ไม่ต้องโฟกัส)
3. **clipboard paste** (Ctrl+A/Ctrl+V, คืน clipboard เดิม) — ต้องโฟกัส
4. **SendInput** unicode (fallback)

- **focus-retry 3x** ผ่าน `UiaSetValueTool.TryConfirmForeground` + `ForceForegroundWindow` ก่อน rung คีย์บอร์ด → transient ที่พบบ่อยสุด (**FocusLost**) ถูกแก้ **ในตัว tool** ก่อนถึงชั้น runtime
- **คง R3′:** ถ้า rung ส่ง input ลงแต่อ่าน read-back ไม่ได้ → ยัง `ReadBackUnavailable` (amber) ไม่ใช่แดง; rung คีย์บอร์ดถูกข้ามถ้า input ลงแล้ว (ไม่พิมพ์ซ้ำ)
- **FocusLost แดง** เกิดเฉพาะเมื่อ **ไม่มี rung ไหนส่ง input ได้เลย** (nativeAccepted=false)
- เพิ่ม evidence `injection_method=<rung>` (telemetry)

> ผลเชิงพฤติกรรม: typing ที่เคย fail เพราะ focus race หรือ control ไม่รับ method เดียว → สำเร็จผ่าน rung ถัดไป **โดยไม่มีการ์ดแดง/ไม่ต้องพึ่ง runtime retry**

## ✅ retryable set (ที่เดียว) — `TransientFailurePolicy.cs`

**retryable (auto-retry ได้):** `FocusLost`, `TargetNotFound`, `StaleObservation`, `StaleWindow`, `ApplicationNotReady` (readiness-timeout)
**non-retryable (แดงทันที + คง R5):** `UnboundInput`, credential-refusal, capture/window validation (`WrongWindow`/`HostWindowTargeted`/…), `EffectMismatch`+`ReadBackUnavailable` (เป็น amber ไม่ใช่ red path), `RetryBudgetExceeded`/`BlindRetryBlocked` (สัญญาณหยุดของ runtime เอง)
+ `ExtractCode()` / `IsRetryableError()` สำหรับดึง code token จาก error string

## ✅ `AgentToolRetryEvent` — event type — `Events/AgentStreamEvents.cs`
`(ToolCallId, ToolName, Attempt, MaxAttempts, Reason)` — additive/backward-compatible (consumer เดิม switch by type, ไม่พังของเดิม); UI รอบ 2b จะ consume

## ✅ tests (24 ใหม่, deterministic) — `Round2aReliabilityTests.cs`
retryable/non-retryable set ครบ · ExtractCode · IsRetryableError · event fields/bus-assignable

---

## ⏸️ R4 — orchestrator auto-retry loop: ทำไมหยุดรายงานก่อนลงมือ

**ที่ตกลงไว้:** full re-capture ที่ orchestrator. พอเจาะโค้ดจริงเพิ่ม พบว่า loop นี้ **ต้อง** ทำทั้งหมดนี้ **ต่อ attempt** ภายใน `ChatOrchestrator.ExecuteToolCallAsync` (streaming method ~200 บรรทัด):

1. re-observe: รัน observation tool (screenshot) ผ่าน **durable pipeline** (`PrepareActionAsync`→execute→`RecordResultAsync`) เพื่อ bump `WorldState.Version` (ไม่งั้นไม่ผ่าน `FreshObservationRequired` :571)
2. re-capture rebind: parse `capture_id` ใหม่จากผล screenshot แล้ว **เขียนทับ args** ของ tool ที่ล้ม (arg-surgery ต่อ tool)
3. re-run tool ผ่าน durable pipeline อีกครั้ง — **นับใน `RetryBudget`** (ห้าม bypass) → เกิน budget = `RetryBudgetExceeded` (แดง)
4. ยุบเป็นการ์ดเดียว: ไม่ยิง `AgentToolCallsEvent`/`AgentToolResultEvent` ต่อ attempt — ยิงแค่ `AgentToolRetryEvent` + result สุดท้าย

**เหตุผลที่ควรรีวิว design ก่อน (ตามกติกา "ห้ามทำเทสเดิมพัง" + "ต่างจากคาด → หยุดรายงาน"):**
- แทรก loop re-enter durable pipeline ต่อ attempt เข้าไปใน method ที่ถือ **idempotency / RetryBudget / reconciliation / anti-false-success** — จุดที่เปราะที่สุดของระบบ (จุดแข็งข้อ 8-12) เสี่ยงทำ invariant พังแบบเงียบ ๆ ถ้ารีบ
- arg-surgery rebind `capture_id` เป็น **tool-specific** (computer_type/click/uia_* คนละ field) — ต้องออกแบบ mapping ให้ปลอดภัย
- deterministic test ของ loop ต้อง mock durable runtime + tool host + observation ทั้งชุด — เป็น test harness ก้อนใหญ่แยกต่างหาก

**ข้อสังเกตเชิงคุณค่า (ทำไม stage ได้โดยไม่เสียเป้าหมายรอบนี้):** transient ที่พบบ่อยสุด (**FocusLost**) ถูก **R2 ladder แก้ในตัว tool แล้ว** (invisible, ไม่มี card churn); เคสที่ ladder แก้ไม่ได้ (`TargetNotFound`/`StaleObservation`) **จำเป็นต้อง fresh capture จริง** ซึ่ง LLM-retry เดิม (gate ถูกต้องอยู่แล้ว) จัดการได้ → R2 + gated LLM-retry ครอบพื้นที่จริงส่วนใหญ่ ทำให้ R4 loop เป็น "ทำให้ invisible + นับ budget เอง" มากกว่าเพิ่มความสามารถใหม่

### design ที่พร้อมลงมือ (ถ้าอนุมัติ round 2a-2)
- extract `RunOneAttemptAsync(prep, tool, args)` ออกจาก `ExecuteToolCallAsync` (execute+record core) แล้วครอบด้วย loop
- insertion: หลัง `RecordResultAsync` (`ChatOrchestrator.cs:625`) — ถ้า `TransientFailurePolicy.IsRetryable(code)` && attempt < budget → emit `AgentToolRetryEvent` → run screenshot (durable) → rebind capture → re-run core
- budget: อ่านจาก `snapshot.Task.RetryBudget` (=5) ผ่าน durableContext; ไม่มี durable = cap คงที่เล็ก (เช่น 3)
- collapse: retry ทั้งหมดใช้ `toolCall.Id` เดิม → ยิงเฉพาะ `AgentToolRetryEvent` + result สุดท้าย (การ์ดเดียว)

---

## ไฟล์ที่แก้/เพิ่ม
| ไฟล์ | ชนิด |
|---|---|
| `Mcp/Tools/ComputerTypeTool.cs` | R2 ladder (แก้ injection block + evidence) |
| `Application/Services/TransientFailurePolicy.cs` | **ใหม่** — retryable set |
| `Events/AgentStreamEvents.cs` | เพิ่ม `AgentToolRetryEvent` |
| `Tests/Agent/Round2aReliabilityTests.cs` | **ใหม่** — 24 tests |

## Build / Test
```
dotnet build SranDesktop.sln  → Build succeeded
dotnet test  SranDesktop.sln  → main 1283 pass / UiTests 4 pass / 0 fail
```
R2 ไม่กระทบ unit test (UIA injection ทดสอบด้วย live UI ไม่ได้ — spot-check บนเครื่องจริงตามเดิม) · คง amber/green ของ R3′ + human-error ของ R5

---

## คำถามก่อนไปต่อ
- **(ก)** อนุมัติ **round 2a-2** ให้ลงมือ R4 orchestrator loop ตาม design ข้างบน (จะทำเป็น change แยก + test harness durable mock) ← ตามที่เลือก full R4
- **(ข)** validate **R2 live** ก่อน (พิมพ์ลง Notepad/แอปจริง) แล้วค่อยตัดสินว่ายังต้อง R4 loop ไหม — เพราะ R2 อาจครอบ transient จริงได้พอแล้ว
- **(ค)** ไป **รอบ 2b (UI retrying state + grouping)** เลย โดยใช้ `AgentToolRetryEvent` ที่มีแล้ว (แม้ publisher ยังรอ R4 loop)

**หยุดรอรีวิว** — ยังไม่แตะ `ExecuteToolCallAsync` (invasive) จนกว่าจะอนุมัติ design
